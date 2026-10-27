"""
The Fair Price Checker: a price someone has been offered, delivery included, against today's prices at every
UK shop we track and the same Price check the product pages use (catalogue/fair_price.py), so the checker and
the product page never give the same price two verdicts.

What it works out (check):

- the cheapest delivered price now at a UK shop we track, and how many have it in stock. eBay and Amazon are
  searches, not shops, and are left out, as is a price kept out of the comparison or doubtful (it may not be
  this product);
- the verdict on the offered price (fair_price.verdict), and the usual price it rests on, labelled with where
  it comes from: our own record's average, Cardmarket's European price or the RRP;
- the scalper line: the offered price is SCALPER_TIMES the usual price or more.

Each check is counted by product and verdict (PriceCheck), never the price or anything about the visitor.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from catalogue import fair_price
from catalogue.models import Listing
from catalogue.pricing import MARKETPLACES

SCALPER_TIMES = Decimal("2")
MAX_PRICE = Decimal("100000")


def read_price(text):
    """A price typed as £89, 89.99, 1,299 or 89,99, in pounds to the penny, or None."""
    cleaned = (text or "").strip().replace("£", "").replace(" ", "").lower().removesuffix("gbp")
    if cleaned.count(",") == 1 and "." not in cleaned and len(cleaned.split(",")[1]) == 2:
        cleaned = cleaned.replace(",", ".")
    cleaned = cleaned.replace(",", "")
    try:
        price = Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return price if Decimal("0") < price <= MAX_PRICE else None


@dataclass
class Check:
    product: object
    offered: Decimal
    verdict: object = None          # fair_price.Verdict, or None without anything to measure by
    cheapest: Decimal | None = None
    cheapest_preorder: bool = False
    in_stock: int = 0               # UK shops with it in stock
    shops: int = 0                  # UK shops listing it, in stock or not
    checked: datetime | None = None
    usual: Decimal | None = None
    usual_from: str = ""            # "record", "market" or "rrp"

    @property
    def scalper(self):
        return self.usual is not None and self.offered >= self.usual * SCALPER_TIMES

    @property
    def key(self):
        """What is counted and drives the next step: a Price check key, "scalper", or "unknown"."""
        if self.scalper:
            return "scalper"
        return self.verdict.key if self.verdict else "unknown"

    @property
    def sold_out(self):
        return self.cheapest is None

    @property
    def at_or_under(self):
        return self.cheapest is not None and self.offered <= self.cheapest

    @property
    def more(self):
        """How much more than the cheapest now the offered price is, or None."""
        if self.cheapest is None or self.offered <= self.cheapest:
            return None
        return self.offered - self.cheapest

    @property
    def times(self):
        return (self.offered / self.usual).quantize(Decimal("0.1")) if self.usual else None


def check(product, offered, today=None):
    """Check for ``product`` at ``offered``. Up to six queries."""
    today = today or timezone.localdate()
    listings = [listing for listing in Listing.objects.filter(product=product).live().select_related("retailer")
                if listing.retailer.source_type not in MARKETPLACES and listing.sanity != Listing.Sanity.EXCLUDED]
    found = Check(product=product, offered=offered, shops=len({listing.retailer_id for listing in listings}))
    found.checked = max((listing.last_checked for listing in listings if listing.last_checked), default=None)
    buyable = [listing for listing in listings
               if listing.is_buyable and listing.delivery_known and listing.sanity != Listing.Sanity.DOUBTFUL]
    in_stock = [listing for listing in buyable if listing.availability == Listing.Availability.IN_STOCK]
    found.in_stock = len({listing.retailer_id for listing in in_stock})
    lowest = min(in_stock or buyable, key=lambda listing: listing.delivered_price, default=None)
    if lowest is not None:
        found.cheapest, found.cheapest_preorder = lowest.delivered_price, not in_stock
    found.verdict = fair_price.verdict(product, offered, today, shops=found.shops)
    verdict = found.verdict
    if verdict is not None:
        if verdict.average is not None:
            found.usual, found.usual_from = verdict.average, "record"
        elif verdict.market is not None:
            found.usual, found.usual_from = verdict.market.gbp, "market"
        elif verdict.rrp is not None:
            found.usual, found.usual_from = verdict.rrp, "rrp"
    return found


def count(request, found):
    """Count one check (PriceCheck), unless a robot or the browser fetching ahead of a tap made it."""
    from catalogue.clicks import prefetching
    from catalogue.insights import bump, is_bot

    from .models import PriceCheck

    if prefetching(request) or is_bot(request):
        return
    bump(PriceCheck, product=found.product, verdict=found.key)
