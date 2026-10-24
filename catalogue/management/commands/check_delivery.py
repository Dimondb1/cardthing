"""
Read each shop's UK delivery charges from the shop itself.

    python manage.py check_delivery            # report only
    python manage.py check_delivery --apply    # also save what was found
    python manage.py check_delivery zatu --apply
    python manage.py check_delivery --apply --unknown   # only shops whose standard charge is not known

The server checks every shop weekly and the shops whose charge is still unknown daily. Shops with an
unknown charge go first, one shop's fault is reported and never stops the rest, and no shop is started once
BUDGET_SECONDS have gone, so the cron's time limit never cuts a run off part way.

Two sources, both the shop's own: its delivery page (for the free-delivery
threshold and, when written out, the standard charge) and, for Shopify shops,
a basket with one cheap product in it, which gives the real standard charge
and confirms the threshold. Every saved figure carries where it came from
and the date, in the retailer's delivery note.
"""

import logging
import time

from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from catalogue import pricing
from catalogue.delivery import check_delivery, shopify_delivery
from catalogue.importers import ImportError_
from catalogue.models import Retailer

logger = logging.getLogger(__name__)

# The cron gives the command 50 minutes. A shop with no delivery page costs up to 27 tries of 30 seconds, so
# no shop is started after 40.
BUDGET_SECONDS = 40 * 60


def describe(cost, cost_source, free_over, free_source):
    parts = []
    if cost is not None:
        parts.append(f"Standard £{cost:.2f} ({cost_source})")
    if free_over is not None:
        parts.append(f"free over £{free_over:.0f} ({free_source})")
    return ", ".join(parts)


class Command(BaseCommand):
    help = "Read UK delivery charges from each shop's delivery page and, for Shopify shops, a test basket."

    def add_arguments(self, parser):
        parser.add_argument("slugs", nargs="*", help="Retailer slugs. Default: every shop shown on the site.")
        parser.add_argument("--apply", action="store_true", help="Save the figures found to each retailer.")
        parser.add_argument("--unknown", action="store_true", help="Only shops whose standard charge is not known.")

    def handle(self, *args, slugs, apply=False, unknown=False, **options):
        # eBay and Amazon give delivery with each listing.
        retailers = (Retailer.objects.filter(is_active=True).exclude(website="")
                     .exclude(source_type__in=pricing.MARKETPLACES)
                     .order_by(F("delivery_cost").asc(nulls_first=True), "name"))
        if slugs:
            retailers = retailers.filter(slug__in=slugs)
        if unknown:
            retailers = retailers.filter(delivery_cost__isnull=True)
        today = timezone.now().strftime("%d %b %Y")
        began = time.monotonic()
        for retailer in retailers:
            if time.monotonic() - began > BUDGET_SECONDS:
                self.stdout.write("Out of time: the other shops are checked at the next run.")
                break
            try:
                self.check(retailer, apply, today)
            except Exception as exc:  # one shop's fault never stops the rest
                logger.exception("Delivery check for %s failed", retailer.name)
                self.stdout.write(f"{retailer.name}: check failed: {exc}")
        if apply:
            from django.core.cache import cache

            cache.clear()

    def check(self, retailer, apply, today):
        """Read one shop's charges and, with ``apply``, save them."""
        base = retailer.website.rstrip("/")
        cost = free_over = None
        cost_source = free_source = ""
        page = check_delivery(base)
        if page is not None:
            cost, cost_source = page.cost, "delivery page"
            free_over, free_source = page.free_over, "delivery page"
        if retailer.source_type == Retailer.Source.SHOPIFY:
            try:
                basket_cost, confirmed = shopify_delivery(retailer.source_url or base, free_over=free_over)
            except ImportError_ as exc:
                basket_cost, confirmed = None, False
                self.stdout.write(f"{retailer.name}: basket check failed: {exc}")
            if basket_cost is not None:
                cost, cost_source = basket_cost, "basket check"
            if confirmed:
                free_source = "delivery page, confirmed by basket"
        found = describe(cost, cost_source, free_over, free_source)
        if not found:
            self.stdout.write(f"{retailer.name}: nothing found. Check by hand: {base}")
            return
        line = f"{retailer.name}: {found}"
        if page is not None:
            line += f" [{page.url}]"
        self.stdout.write(line)
        if not apply:
            return
        changed = []
        if cost is not None and cost != retailer.delivery_cost:
            retailer.delivery_cost, _ = cost, changed.append("delivery_cost")
        if free_over is not None and free_over != retailer.free_delivery_over:
            retailer.free_delivery_over, _ = free_over, changed.append("free_delivery_over")
        note = f"{found}, read {today}"[:120]
        if note != retailer.delivery_note:
            retailer.delivery_note, _ = note, changed.append("delivery_note")
        if changed:
            retailer.save(update_fields=changed)
            self.stdout.write(f"  saved: {', '.join(changed)}")
            if {"delivery_cost", "free_delivery_over"} & set(changed):
                pricing.apply_delivery_rules(retailer)
