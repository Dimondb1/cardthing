"""
Load demo data for local development and design review.

The retailers are fictional and use the reserved .example domain. Prices,
stock and history are generated. Do not run this against a live database.
"""

import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from catalogue.models import (
    DailyLowestPrice,
    Game,
    Listing,
    OutboundClick,
    Product,
    ProductSet,
    Retailer,
)

T = Product.Type

GAMES = [
    # name, short name, slug, search aliases
    ("Pokémon", "Pokémon", "pokemon", "pokemon, ptcg, pkmn"),
    ("Magic: The Gathering", "Magic", "magic-the-gathering", "mtg, magic"),
    ("One Piece Card Game", "One Piece", "one-piece", "optcg"),
    ("Disney Lorcana", "Lorcana", "lorcana", ""),
]

SETS = {
    "pokemon": [
        ("Destined Rivals", "DRI", date(2025, 5, 30)),
        ("Journey Together", "JTG", date(2025, 3, 28)),
        ("Prismatic Evolutions", "PRE", date(2025, 1, 17)),
        ("Surging Sparks", "SSP", date(2024, 11, 8)),
    ],
    "magic-the-gathering": [
        ("Edge of Eternities", "EOE", date(2025, 8, 1)),
        ("Final Fantasy", "FIN", date(2025, 6, 13)),
        ("Tarkir: Dragonstorm", "TDM", date(2025, 4, 11)),
    ],
    "one-piece": [
        ("A Fist of Divine Speed", "OP-11", date(2025, 6, 6)),
        ("Royal Blood", "OP-10", date(2025, 3, 21)),
    ],
    "lorcana": [
        ("Fabled", "", date(2025, 9, 5)),
        ("Reign of Jafar", "", date(2025, 5, 30)),
        ("Archazia's Island", "", date(2025, 3, 7)),
    ],
}

# set name, product name, type, typical price
PRODUCTS = [
    ("Destined Rivals", "Destined Rivals Booster Box", T.BOOSTER_BOX, 164),
    ("Destined Rivals", "Destined Rivals Elite Trainer Box", T.ELITE_TRAINER_BOX, 56),
    ("Destined Rivals", "Destined Rivals Booster Bundle", T.BUNDLE, 31),
    ("Journey Together", "Journey Together Booster Box", T.BOOSTER_BOX, 152),
    ("Journey Together", "Journey Together Elite Trainer Box", T.ELITE_TRAINER_BOX, 50),
    ("Journey Together", "Journey Together Booster Bundle", T.BUNDLE, 28),
    ("Prismatic Evolutions", "Prismatic Evolutions Elite Trainer Box", T.ELITE_TRAINER_BOX, 86),
    ("Prismatic Evolutions", "Prismatic Evolutions Booster Bundle", T.BUNDLE, 46),
    ("Prismatic Evolutions", "Prismatic Evolutions Super-Premium Collection", T.COLLECTION_BOX, 140),
    ("Surging Sparks", "Surging Sparks Booster Box", T.BOOSTER_BOX, 156),
    ("Surging Sparks", "Surging Sparks Elite Trainer Box", T.ELITE_TRAINER_BOX, 48),
    ("Surging Sparks", "Surging Sparks Booster Bundle", T.BUNDLE, 27),
    ("Edge of Eternities", "Edge of Eternities Play Booster Box", T.BOOSTER_BOX, 132),
    ("Edge of Eternities", "Edge of Eternities Collector Booster Box", T.BOOSTER_BOX, 265),
    ("Edge of Eternities", "Edge of Eternities Bundle", T.BUNDLE, 46),
    ("Final Fantasy", "Final Fantasy Play Booster Box", T.BOOSTER_BOX, 182),
    ("Final Fantasy", "Final Fantasy Collector Booster Box", T.BOOSTER_BOX, 420),
    ("Final Fantasy", "Final Fantasy Bundle", T.BUNDLE, 62),
    ("Final Fantasy", "Final Fantasy Commander Deck: Revival Trance", T.DECK, 46),
    ("Tarkir: Dragonstorm", "Tarkir: Dragonstorm Play Booster Box", T.BOOSTER_BOX, 126),
    ("A Fist of Divine Speed", "A Fist of Divine Speed Booster Box (OP-11)", T.BOOSTER_BOX, 96),
    ("Royal Blood", "Royal Blood Booster Box (OP-10)", T.BOOSTER_BOX, 94),
    ("Fabled", "Fabled Booster Box", T.BOOSTER_BOX, 112),
    ("Fabled", "Fabled Illumineer's Trove", T.COLLECTION_BOX, 46),
    ("Reign of Jafar", "Reign of Jafar Booster Box", T.BOOSTER_BOX, 106),
    ("Archazia's Island", "Archazia's Island Booster Box", T.BOOSTER_BOX, 104),
    ("Archazia's Island", "Archazia's Island Gift Set", T.GIFT_SET, 30),
]

# name, domain, delivery charge, free delivery threshold, note
RETAILERS = [
    ("Harbour Games", "harbourgames.example", "3.49", 50, "Free delivery over £50"),
    ("Northgate Cards", "northgatecards.example", "2.99", 75, "Free delivery over £75"),
    ("Kestrel Collectables", "kestrelcollectables.example", "4.99", 100, "Free delivery over £100"),
    ("Blue Door Games", "bluedoorgames.example", "0.00", 0, "Free delivery on everything"),
    ("Tollgate TCG", "tollgatetcg.example", "3.99", 60, "Free delivery over £60"),
    ("Marlow Card Co.", "marlowcards.example", "2.49", None, "£2.49 delivery on every order"),
]

# Special cases so every state appears in the demo.
OUT_OF_STOCK_EVERYWHERE = "Prismatic Evolutions Super-Premium Collection"
NO_LISTINGS = "Archazia's Island Gift Set"
PREORDER_ONLY = "Fabled Illumineer's Trove"
FEATURED_DROPS = {
    "Prismatic Evolutions Elite Trainer Box": 0.16,
    "Final Fantasy Play Booster Box": 0.11,
    "Destined Rivals Booster Box": 0.08,
    "Edge of Eternities Bundle": 0.13,
    "Royal Blood Booster Box (OP-10)": 0.07,
    "Surging Sparks Elite Trainer Box": 0.09,
}
POPULAR_WEIGHTS = {
    "Prismatic Evolutions Elite Trainer Box": 40,
    "Final Fantasy Collector Booster Box": 26,
    "Destined Rivals Elite Trainer Box": 22,
    "Final Fantasy Play Booster Box": 18,
    "Journey Together Booster Box": 12,
    "Fabled Booster Box": 11,
    "Edge of Eternities Play Booster Box": 9,
    "A Fist of Divine Speed Booster Box (OP-11)": 8,
}

PENNY = Decimal("0.01")


def shop_price(value):
    """Round to prices shops actually use: .00, .49, .95 or .99."""
    pounds = int(value)
    endings = [Decimal("0.00"), Decimal("0.49"), Decimal("0.95"), Decimal("0.99")]
    candidates = [Decimal(pounds) + end for end in endings] + [Decimal(pounds + 1)]
    return min(candidates, key=lambda c: abs(c - Decimal(str(value))))


class Command(BaseCommand):
    help = "Load fictional demo retailers, products, prices and history."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete every game, product, retailer and price first.",
        )
        parser.add_argument("--seed", type=int, default=7, help="Random seed.")

    def handle(self, *args, flush=False, seed=7, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo only runs with DJANGO_DEBUG on.")
        if Product.objects.exists() and not flush:
            raise CommandError("Products already exist. Use --flush to replace them.")

        rng = random.Random(seed)
        with transaction.atomic():
            if flush:
                OutboundClick.objects.all().delete()
                DailyLowestPrice.objects.all().delete()
                Listing.objects.all().delete()
                Product.objects.all().delete()
                ProductSet.objects.all().delete()
                Game.objects.all().delete()
                Retailer.objects.all().delete()
            self._load(rng)

        self.stdout.write(
            f"Loaded {Game.objects.count()} games, {Product.objects.count()} products, "
            f"{Retailer.objects.count()} retailers and {Listing.objects.count()} listings."
        )

    def _load(self, rng):
        now = timezone.now()
        today = timezone.localdate()

        games = {}
        for order, (name, short, slug, aliases) in enumerate(GAMES, start=1):
            games[slug] = Game.objects.create(
                name=name, short_name=short, slug=slug, search_aliases=aliases, sort_order=order
            )

        sets = {}
        for game_slug, rows in SETS.items():
            for name, code, released in rows:
                sets[name] = ProductSet.objects.create(
                    game=games[game_slug],
                    name=name,
                    slug=slugify(name),
                    code=code,
                    release_date=released,
                )

        retailers = []
        for name, domain, delivery, free_over, note in RETAILERS:
            retailer = Retailer.objects.create(
                name=name,
                slug=slugify(name),
                website=f"https://www.{domain}/",
                delivery_note=note,
            )
            retailer.demo_delivery = Decimal(delivery)
            retailer.demo_free_over = free_over
            retailers.append(retailer)

        products = {}
        for set_name, name, product_type, typical in PRODUCTS:
            product_set = sets[set_name]
            product = Product.objects.create(
                game=product_set.game,
                product_set=product_set,
                name=name,
                slug=slugify(name),
                product_type=product_type,
            )
            products[name] = product
            if name == NO_LISTINGS:
                continue
            lowest = self._listings(rng, product, typical, retailers, now)
            self._history(rng, product, typical, lowest, today)

        self._clicks(rng, products, now)

    def _listings(self, rng, product, typical, retailers, now):
        chosen = rng.sample(retailers, rng.randint(3, 6))
        lowest = None
        for index, retailer in enumerate(chosen):
            price = shop_price(typical * rng.uniform(0.9, 1.14))
            delivery = retailer.demo_delivery
            if retailer.demo_free_over is not None and price >= retailer.demo_free_over:
                delivery = Decimal("0.00")

            availability = Listing.Availability.IN_STOCK
            if product.name == OUT_OF_STOCK_EVERYWHERE:
                availability = Listing.Availability.OUT_OF_STOCK
            elif product.name == PREORDER_ONLY:
                availability = Listing.Availability.PREORDER
            elif rng.random() < 0.18:
                availability = Listing.Availability.OUT_OF_STOCK

            checked = now - timedelta(minutes=rng.randint(4, 300))
            if index == len(chosen) - 1 and rng.random() < 0.25:
                checked = now - timedelta(days=rng.randint(4, 9))

            Listing.objects.create(
                product=product,
                retailer=retailer,
                url=f"{retailer.website}products/{product.slug}",
                price=price,
                delivery_cost=delivery,
                availability=availability,
                last_checked=checked,
            )
            fresh = checked >= now - timedelta(hours=settings.RIPRAPTOR_STALE_AFTER_HOURS)
            if availability != Listing.Availability.OUT_OF_STOCK and fresh:
                total = price + delivery
                lowest = total if lowest is None else min(lowest, total)
        return lowest

    def _history(self, rng, product, typical, lowest, today):
        days = settings.RIPRAPTOR_HISTORY_DAYS
        trending = settings.RIPRAPTOR_TRENDING_DAYS
        end_price = lowest if lowest is not None else Decimal(str(typical))

        drop = FEATURED_DROPS.get(product.name)
        if drop:
            week_ago_price = float(end_price) / (1 - drop)
        else:
            week_ago_price = float(end_price) * rng.uniform(0.97, 1.0)

        # Walk backwards from today so the recent part of the chart matches
        # the listings.
        series = {}
        change_day = rng.randint(1, trending - 2)
        value = float(end_price)
        for offset in range(days):
            day = today - timedelta(days=offset)
            if offset <= change_day:
                value = float(end_price)
            elif offset <= trending:
                value = week_ago_price
            else:
                value = max(typical * 0.82, value * rng.uniform(0.985, 1.02))
            series[day] = Decimal(str(value)).quantize(PENNY, rounding=ROUND_HALF_UP)

        if lowest is None:
            # Out of stock everywhere: history stops a couple of weeks ago.
            series = {day: price for day, price in series.items() if day < today - timedelta(days=12)}

        DailyLowestPrice.objects.bulk_create(
            DailyLowestPrice(product=product, date=day, price=shop_price(float(price)))
            for day, price in series.items()
        )
        if lowest is not None:
            DailyLowestPrice.objects.filter(product=product, date=today).update(price=lowest)

    def _clicks(self, rng, products, now):
        clicks = []
        for name, weight in POPULAR_WEIGHTS.items():
            product = products[name]
            listings = list(product.listings.all())
            if not listings:
                continue
            for _ in range(weight):
                listing = rng.choice(listings)
                clicks.append(
                    OutboundClick(
                        listing=listing,
                        product=product,
                        retailer=listing.retailer,
                        created_at=now - timedelta(minutes=rng.randint(10, 60 * 24 * 6)),
                    )
                )
        OutboundClick.objects.bulk_create(clicks)
