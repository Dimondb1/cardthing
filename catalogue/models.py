from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote, urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, F, Min, Q
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from .search import build_search_text


def stale_cutoff(now=None):
    """Listings checked before this moment are out of date."""
    hours = settings.RIPRAPTOR_STALE_AFTER_HOURS
    return (now or timezone.now()) - timedelta(hours=hours)


class Game(models.Model):
    name = models.CharField(max_length=80, unique=True)
    short_name = models.CharField(
        max_length=40,
        blank=True,
        help_text="Used where space is tight, for example “Magic”. Optional.",
    )
    slug = models.SlugField(max_length=80, unique=True)
    search_aliases = models.CharField(
        max_length=200,
        blank=True,
        help_text="Other words people search for, separated by commas. For example: mtg, magic",
    )
    sort_order = models.PositiveSmallIntegerField(
        default=0, help_text="Lower numbers are listed first."
    )
    is_active = models.BooleanField("show on site", default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

    @property
    def display_short(self):
        return self.short_name or self.name

    def get_absolute_url(self):
        return reverse("web:game", args=[self.slug])


class ProductSet(models.Model):
    game = models.ForeignKey(Game, on_delete=models.PROTECT, related_name="sets")
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120)
    code = models.CharField(
        max_length=20,
        blank=True,
        help_text="Set code, for example SV8.5 or OP-10. Optional.",
    )
    release_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["game", F("release_date").desc(nulls_last=True), "name"]
        unique_together = [("game", "slug")]
        verbose_name = "set"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("web:set", args=[self.game.slug, self.slug])


class ProductQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True, game__is_active=True)

    def with_prices(self):
        """Annotate the cheapest current delivered price and stock counts."""
        cutoff = stale_cutoff()
        live = Q(listings__is_active=True, listings__retailer__is_active=True)
        buyable = live & Q(
            listings__last_checked__gte=cutoff,
            listings__availability__in=Listing.BUYABLE,
            listings__sanity__in=Listing.COUNTED,
        )
        known = Q(listings__delivery_known=True)
        return self.annotate(
            # The cheapest confirmed delivered price; without one, the cheapest item price of a shop whose
            # delivery is unknown, which is shown as "plus delivery". Lists sort on what they show.
            lowest_known=Min("listings__delivered_price", filter=buyable & known),
            lowest_price=Coalesce(
                Min("listings__delivered_price", filter=buyable & known),
                Min("listings__price", filter=buyable & Q(listings__delivery_known=False)),
            ),
            in_stock_count=Count(
                "listings",
                filter=buyable & Q(listings__availability=Listing.Availability.IN_STOCK),
                distinct=True,
            ),
            preorder_count=Count(
                "listings",
                filter=buyable & Q(listings__availability=Listing.Availability.PREORDER),
                distinct=True,
            ),
            listing_count=Count("listings", filter=live, distinct=True),
            # Cheapest delivered price any shop has listed, whether or not it is in stock now, leaving out a
            # price kept out of the comparison.
            last_price=Min("listings__delivered_price", filter=live & Q(listings__sanity__in=Listing.COUNTED)),
        )

    def with_release(self):
        return self.annotate(release=Coalesce("release_date", "product_set__release_date"))

    def for_lists(self):
        """Everything a product row or tile needs, in one query."""
        return (
            self.active()
            .select_related("game", "product_set")
            .with_prices()
            .with_release()
        )


class Product(models.Model):
    class Type(models.TextChoices):
        BOOSTER_BOX = "booster_box", "Booster box"
        ELITE_TRAINER_BOX = "elite_trainer_box", "Elite Trainer Box"
        BUNDLE = "bundle", "Bundle"
        COLLECTION_BOX = "collection_box", "Collection box"
        DECK = "deck", "Deck"
        TIN = "tin", "Tin"
        BOOSTER_PACK = "booster_pack", "Booster pack"
        GIFT_SET = "gift_set", "Gift set"
        COLLECTOR_BOOSTER_BOX = "collector_booster_box", "Collector booster box"
        COLLECTOR_BOOSTER_PACK = "collector_booster_pack", "Collector booster pack"
        OTHER = "other", "Other"

    game = models.ForeignKey(Game, on_delete=models.PROTECT, related_name="products")
    product_set = models.ForeignKey(
        ProductSet,
        on_delete=models.PROTECT,
        related_name="products",
        null=True,
        blank=True,
        verbose_name="set",
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    product_type = models.CharField(max_length=30, choices=Type.choices, db_index=True)
    image = models.ImageField(
        upload_to="products/",
        blank=True,
        help_text="Square or portrait image on a plain background works best. "
        "Without one, an outline of the product type is shown.",
    )
    image_url = models.URLField(
        "image from a retailer feed",
        max_length=1000,
        blank=True,
        help_text="Filled in by price imports when no image has been uploaded. An uploaded "
        "image always takes priority.",
    )
    ean = models.CharField("barcode", max_length=20, blank=True, db_index=True)
    amazon_asin = models.CharField(
        "Amazon product id", max_length=20, blank=True,
        help_text="Found by the Amazon import. Clear it to make the import look again.",
    )
    amazon_checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    ebay_checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    release_date = models.DateField(
        null=True, blank=True, help_text="Leave empty to use the set's release date."
    )
    is_active = models.BooleanField("show on site", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    search_text = models.TextField(editable=False, blank=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.product_set_id and self.game_id and self.product_set.game_id != self.game_id:
            raise ValidationError({"product_set": "This set belongs to a different game."})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:220]
        self.search_text = build_search_text(self)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "search_text" not in update_fields:
            kwargs["update_fields"] = list(update_fields) + ["search_text"]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("web:product", args=[self.slug])

    @property
    def type_name(self):
        """The product type in the words this game's players use."""
        from .types import type_label

        return type_label(self.game.slug, self.product_type)

    @property
    def image_src(self):
        """Address of the best image we have, or an empty string."""
        if self.image:
            return self.image.url
        return self.image_url

    @property
    def effective_release_date(self):
        if self.release_date:
            return self.release_date
        return self.product_set.release_date if self.product_set else None


class Retailer(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    website = models.URLField()
    affiliate_url_template = models.CharField(
        "affiliate link format",
        max_length=500,
        blank=True,
        help_text="Leave empty to link straight to the retailer. Otherwise put {url} "
        "where the product link goes, for example "
        "https://network.example/click?id=123&url={url}. The link is encoded for you.",
    )
    delivery_note = models.CharField(
        max_length=120,
        blank=True,
        help_text="Shown on the how it works page, for example “Free delivery over £50”.",
    )
    delivery_cost = models.DecimalField(
        "standard delivery",
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
        help_text="Charge to deliver one item to a UK address. Used for imported prices. 0 for free delivery. "
        "Leave empty when not known: prices then show as plus delivery and never count as the cheapest delivered.",
    )
    delivery_cost_up_to = models.DecimalField(
        "standard charge only up to",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="When the standard charge is only known for orders under a value (larger orders cost an "
        "unpublished amount), that value. Items above it show as plus delivery. Leave empty otherwise.",
    )
    free_delivery_over = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Orders of this value or more are delivered free. Leave empty if never.",
    )

    class Source(models.TextChoices):
        MANUAL = "manual", "Entered by hand"
        SHOPIFY = "shopify", "Shopify store"
        FEED = "feed", "Product feed (CSV or Google Shopping XML)"
        AMAZON = "amazon", "Amazon (Product Advertising API)"
        EBAY = "ebay", "eBay (Browse API)"
        WEBSITE = "website", "Website (sitemap and product pages)"

    source_type = models.CharField(
        "price source", max_length=20, choices=Source.choices, default=Source.MANUAL
    )
    source_url = models.URLField(
        "source address",
        max_length=500,
        blank=True,
        help_text="Shopify: the shop address, for example https://shop.example/. "
        "Feed: the CSV or Google Shopping XML address. Website: the shop address; product pages are found through "
        "its sitemap and read for schema.org product data. Leave empty for a local file "
        "passed to import_prices.",
    )
    collection = models.SlugField(
        "Shopify collection",
        max_length=120,
        blank=True,
        help_text="Shopify only: read this collection's products instead of the whole shop, for example "
        "trading-card-games for a board game shop with one card game section. Products outside it are "
        "treated as not stocked.",
    )
    session_url = models.URLField(
        "visit first",
        max_length=500,
        blank=True,
        help_text="An address the importer opens before reading product pages, keeping the cookies it "
        "sets. For a shop that shows each visitor their own currency, this is its link to switch "
        "to pounds, for example https://shop.example/changecurrency/3?returnUrl=%2F.",
    )
    is_active = models.BooleanField("show on site", default=True)

    # When and how often the shop is read. The hourly import and the background reader follow the same rules.
    read_every_minutes = models.PositiveIntegerField(
        "read every (minutes)",
        default=60,
        help_text="How often to read this shop's prices. A shop that takes a long time to read is read less "
        "often: never more than a third of the time.",
    )
    next_read_at = models.DateTimeField("next read", null=True, blank=True, db_index=True)
    last_read_seconds = models.PositiveIntegerField("last read took (seconds)", null=True, blank=True)
    last_ok_at = models.DateTimeField("last read that worked", null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    error_streak = models.PositiveSmallIntegerField("failed reads in a row", default=0)
    backoff_until = models.DateTimeField("waiting after errors until", null=True, blank=True)
    reading_paused = models.BooleanField("reading paused", default=False)

    # A failed read waits 5 minutes, then 10, 20 and so on up to 6 hours. A shop that says it is being
    # asked too often (HTTP 429) waits at least 30 minutes at once.
    BACKOFF_FIRST = timedelta(minutes=5)
    BACKOFF_LONGEST = timedelta(hours=6)
    BACKOFF_TOO_MANY = timedelta(minutes=30)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @classmethod
    def due(cls, now=None):
        """Shops whose next read has come and that are not waiting after errors or paused.

        Marketplaces come first (they have a daily allowance), then the shop waiting longest, shops never
        read before any other, then by name.
        """
        now = now or timezone.now()
        return (
            cls.objects.filter(is_active=True, reading_paused=False)
            .exclude(source_type=cls.Source.MANUAL)
            .filter(Q(next_read_at__isnull=True) | Q(next_read_at__lte=now))
            .filter(Q(backoff_until__isnull=True) | Q(backoff_until__lte=now))
            .alias(
                marketplace=models.Case(
                    models.When(source_type__in=[cls.Source.EBAY, cls.Source.AMAZON], then=0),
                    default=1,
                    output_field=models.IntegerField(),
                )
            )
            .order_by("marketplace", F("next_read_at").asc(nulls_first=True), "name")
        )

    def cadence_for(self, finished, seconds):
        """When to read next after a read that ended at ``finished`` and took ``seconds``.

        The setting, or three times the read time when that is longer, so a slow shop is never being read
        more than a third of the time.
        """
        minutes = max(self.read_every_minutes, 3 * (seconds or 0) / 60)
        return finished + timedelta(minutes=minutes)

    def read_ok(self, now, seconds, ok_at=None):
        """Record a read that worked: errors forgotten, next read set by the cadence."""
        self.error_streak = 0
        self.backoff_until = None
        self.last_error = ""
        self.last_ok_at = ok_at or now
        self.last_read_seconds = int(seconds)
        self.next_read_at = self.cadence_for(now, seconds)
        self.save(update_fields=[
            "error_streak", "backoff_until", "last_error", "last_ok_at", "last_read_seconds", "next_read_at",
        ])

    def read_failed(self, now, status=None, error=""):
        """Record a failed read and wait longer after each one in a row."""
        self.error_streak = min(self.error_streak + 1, 32767)
        wait = min(self.BACKOFF_FIRST * 2 ** min(self.error_streak - 1, 20), self.BACKOFF_LONGEST)
        if status == 429:
            wait = max(wait, self.BACKOFF_TOO_MANY)
        self.backoff_until = now + wait
        if error:
            self.last_error = error[:300]
        self.save(update_fields=["error_streak", "backoff_until", "last_error"])

    def delivery_for(self, price):
        """Delivery charge for one item at ``price`` under this retailer's rules, or None when not known.

        Never guessed: a missing charge is unknown, not free.
        """
        if self.free_delivery_over is not None and price >= self.free_delivery_over:
            return Decimal("0.00")
        if self.delivery_cost_up_to is not None and price > self.delivery_cost_up_to:
            return None
        return self.delivery_cost

    @property
    def domain(self):
        host = urlparse(self.website).netloc
        return host.removeprefix("www.")

    def outbound_url(self, product_url):
        template = self.affiliate_url_template.strip()
        if not template:
            return product_url
        return template.replace("{url}", quote(product_url, safe=""))


class ProductAlias(models.Model):
    """An old product address that now points at a merged product, so links keep working."""

    slug = models.SlugField(max_length=220, unique=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="aliases")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "old product address"
        verbose_name_plural = "old product addresses"

    def __str__(self):
        return f"{self.slug} -> {self.product.slug}"


class ListingQuerySet(models.QuerySet):
    def live(self):
        return self.filter(is_active=True, retailer__is_active=True)

    def current(self):
        return self.live().filter(last_checked__gte=stale_cutoff())

    def buyable(self):
        """Current, in stock or on pre-order, and not kept out of the comparison as impossible."""
        return self.current().filter(availability__in=Listing.BUYABLE).exclude(sanity=Listing.Sanity.EXCLUDED)


class Listing(models.Model):
    """One retailer's offer for one product."""

    class Availability(models.TextChoices):
        IN_STOCK = "in_stock", "In stock"
        PREORDER = "preorder", "Pre-order"
        OUT_OF_STOCK = "out_of_stock", "Out of stock"

    BUYABLE = [Availability.IN_STOCK, Availability.PREORDER]

    class Sanity(models.TextChoices):
        OK = "ok", "OK"
        DOUBTFUL = "doubtful", "Doubtful"
        EXCLUDED = "excluded", "Excluded"

    # Verdicts that still count in the comparison; an excluded price is kept out of it.
    COUNTED = [Sanity.OK, Sanity.DOUBTFUL]
    # Every reason the band of products of the same kind gives for keeping a price out starts with this
    # (catalogue/sanity.py), so the product page can say why without claiming other shops were compared.
    BAND_KEPT_OUT = "far below every "

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="listings")
    retailer = models.ForeignKey(Retailer, on_delete=models.CASCADE, related_name="listings")
    url = models.URLField("product link", max_length=1000)
    title = models.CharField(max_length=300, blank=True, help_text="The shop's own name for it, kept so a match can be re-judged when the rules change.")
    price = models.DecimalField(max_digits=8, decimal_places=2)
    delivery_cost = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Standard delivery for one item to a UK address. 0 for free delivery.",
    )
    delivery_known = models.BooleanField(
        default=True,
        help_text="Untick when the delivery charge is not known. The price then shows as plus delivery, "
        "is listed after confirmed delivered prices and never counts as the cheapest.",
    )
    delivered_price = models.GeneratedField(
        expression=F("price") + F("delivery_cost"),
        output_field=models.DecimalField(max_digits=9, decimal_places=2),
        db_persist=True,
    )
    availability = models.CharField(
        max_length=20, choices=Availability.choices, default=Availability.IN_STOCK, db_index=True
    )
    last_checked = models.DateTimeField(default=timezone.now, db_index=True)
    back_in_stock_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="When this shop last went from not having it to having it in stock.",
    )
    is_active = models.BooleanField("show on site", default=True)
    # The verdict from the other shops' prices (catalogue/sanity.py). Written only when it changes.
    sanity = models.CharField(
        "price verdict", max_length=10, choices=Sanity.choices, default=Sanity.OK, db_index=True,
        help_text="Excluded prices are kept out of the comparison; doubtful ones are listed under Things to check.",
    )
    sanity_reason = models.CharField("verdict reason", max_length=160, blank=True)
    sanity_ratio = models.DecimalField(
        "price against the others", max_digits=7, decimal_places=2, null=True, blank=True,
        help_text="This price divided by what the other shops charge.",
    )
    sanity_at = models.DateTimeField("verdict changed", null=True, blank=True)
    last_ok_price = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    trusted_price = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="A price the owner confirmed on Things to check. It counts while it moves less than 10% for 30 days.",
    )
    trusted_at = models.DateTimeField(null=True, blank=True)

    objects = ListingQuerySet.as_manager()

    class Meta:
        ordering = ["delivered_price"]
        unique_together = [("product", "retailer")]

    def __str__(self):
        return f"{self.product} at {self.retailer}"

    @property
    def total(self):
        """Delivered price, also correct on an instance that has not been reloaded."""
        return self.price + self.delivery_cost

    @property
    def shown_price(self):
        """The price a visitor compares: delivered when delivery is known, the item price otherwise."""
        return self.delivered_price if self.delivery_known else self.price

    @property
    def is_stale(self):
        return self.last_checked < stale_cutoff()

    @property
    def is_buyable(self):
        return (
            self.is_active
            and self.retailer.is_active
            and not self.is_stale
            and self.availability in self.BUYABLE
            and self.sanity != self.Sanity.EXCLUDED
        )

    @property
    def kept_out_by_band(self):
        """Kept out for being far below products of the same kind, not for being far from other shops."""
        return self.sanity == self.Sanity.EXCLUDED and self.sanity_reason.startswith(self.BAND_KEPT_OUT)

    def get_outbound_url(self):
        return reverse("web:go", args=[self.pk])


class ImportRun(models.Model):
    """One run of import_prices for one retailer."""

    retailer = models.ForeignKey(Retailer, on_delete=models.CASCADE, related_name="import_runs")
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    offers_found = models.PositiveIntegerField(default=0)
    listings_updated = models.PositiveIntegerField(default=0)
    unmatched = models.TextField(
        blank=True,
        help_text="Shop products that matched nothing. To include one, open our product in admin, "
        "add a listing for this retailer and paste the link shown here.",
    )
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "price import"

    def __str__(self):
        return f"{self.retailer} at {self.started_at:%d %b %H:%M}"

    @property
    def ok(self):
        return self.finished_at is not None and not self.error


class ShopProduct(models.Model):
    """A product a retailer sells that price imports could not match by barcode or link.

    The importer suggests one of our products from the name. Confident matches
    are linked automatically; the rest wait here for a person to confirm.
    """

    class Status(models.TextChoices):
        REVIEW = "review", "Needs a decision"
        LINKED = "linked", "Linked"
        IGNORED = "ignored", "Not one of ours"

    retailer = models.ForeignKey(Retailer, on_delete=models.CASCADE, related_name="shop_products")
    title = models.CharField(max_length=300)
    url = models.URLField(max_length=1000)
    price = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    image_url = models.URLField(max_length=1000, blank=True)
    suggested = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="our product",
        help_text="The importer's best guess. Change it if it is wrong, then use the Link action.",
    )
    confidence = models.PositiveSmallIntegerField(default=0, help_text="0 to 100.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REVIEW, db_index=True)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-confidence", "title"]
        unique_together = [("retailer", "url")]
        verbose_name = "shop product to review"
        verbose_name_plural = "shop products to review"

    def __str__(self):
        return f"{self.title} ({self.retailer})"


class DailyLowestPrice(models.Model):
    """The cheapest delivered price seen for a product on one day."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="daily_prices")
    date = models.DateField()
    price = models.DecimalField(max_digits=9, decimal_places=2)

    class Meta:
        ordering = ["-date"]
        unique_together = [("product", "date")]
        verbose_name = "daily lowest price"

    def __str__(self):
        return f"{self.product} on {self.date}: £{self.price}"


class TypeBand(models.Model):
    """The usual price range of one kind of product in one game, or in one set of it.

    Worked out each night from the shops' counted prices (catalogue/sanity.py rebuild_bands), so a
    price with no other shop to compare against can still be judged against products like it. A row
    with no set is the band for the whole game.
    """

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="type_bands")
    product_type = models.CharField(max_length=30, choices=Product.Type.choices)
    product_set = models.ForeignKey(
        ProductSet, on_delete=models.CASCADE, null=True, blank=True, related_name="type_bands", verbose_name="set"
    )
    n = models.PositiveIntegerField("products")
    p10 = models.DecimalField("cheapest tenth", max_digits=9, decimal_places=2)
    median = models.DecimalField(max_digits=9, decimal_places=2)
    p90 = models.DecimalField("dearest tenth", max_digits=9, decimal_places=2)
    computed_at = models.DateTimeField()

    class Meta:
        ordering = ["game", "product_type", "product_set"]
        unique_together = [("game", "product_type", "product_set")]
        verbose_name = "price band"

    def __str__(self):
        where = self.product_set or self.game
        return f"{self.get_product_type_display()} in {where}: £{self.p10} to £{self.p90}"


class OutboundClick(models.Model):
    """A visit to a retailer from RipRaptor. No personal data is stored."""

    listing = models.ForeignKey(Listing, on_delete=models.SET_NULL, null=True, blank=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="outbound_clicks")
    retailer = models.ForeignKey(Retailer, on_delete=models.CASCADE, related_name="outbound_clicks")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    source = models.CharField(max_length=12, blank=True)   # "watchlist" when the click came from a saved list

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "click to retailer"
        verbose_name_plural = "clicks to retailers"

    def __str__(self):
        return f"{self.product} to {self.retailer}"


class Restock(models.Model):
    """One time a shop went from not having a product to having it in stock.

    Written by ``pricing.record_check``. A listing that flips back and forth
    within two hours counts once, and marketplaces (eBay, Amazon) are left
    out because their stock is many sellers, not one shop restocking.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="restocks")
    retailer = models.ForeignKey(Retailer, on_delete=models.CASCADE, related_name="restocks")
    listing = models.ForeignKey(Listing, on_delete=models.SET_NULL, null=True, blank=True, related_name="restocks")
    at = models.DateTimeField(default=timezone.now, db_index=True)
    price = models.DecimalField(max_digits=9, decimal_places=2, help_text="Delivered price when it came back.")
    delivery_known = models.BooleanField(default=True, help_text="Off when the price is the item price alone.")

    class Meta:
        ordering = ["-at"]
        verbose_name = "restock"

    def __str__(self):
        return f"{self.product} at {self.retailer}, {self.at:%d %b %H:%M}"


def alert_token():
    import secrets

    return secrets.token_urlsafe(24)


class StockAlert(models.Model):
    """One email address waiting to hear when one product is back in stock.

    The address is kept for this and nothing else. It is confirmed by a link
    before anything else is sent, deleted as soon as the one back-in-stock
    email has gone, and deleted unconfirmed after a week or confirmed after
    six months. Every email carries a link that deletes it at once.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="stock_alerts")
    email = models.EmailField(max_length=254)
    token = models.CharField(max_length=40, unique=True, default=alert_token)
    created_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("product", "email")]
        ordering = ["-created_at"]
        verbose_name = "back in stock alert"

    def __str__(self):
        return f"{self.product} for {self.email}"


class DailyPageView(models.Model):
    """How many times a kind of page was opened on one day. No personal data."""

    class Kind(models.TextChoices):
        HOME = "home", "Home"
        PRODUCT = "product", "Product"
        GAME = "game", "Game"
        SET = "set", "Set"
        SEARCH = "search", "Search"
        SWIPE = "swipe", "Swipe"
        DEALS = "deals", "Deals"
        NEW = "new", "Latest drops"
        WATCHLIST = "watchlist", "Watchlist"
        WATCHED = "watched", "Watchlist rows"   # one per product on a loaded watchlist, keyed by slug
        INSTALL = "install", "Home screen"     # keyed by event: shown, added, dismissed, opened
        ALERTS = "alerts", "Stock alerts"     # keyed by event: asked, confirmed, sent, stopped
        OTHER = "other", "Other"

    date = models.DateField(db_index=True)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    key = models.CharField(max_length=220, blank=True)   # product, game or set slug
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("date", "kind", "key")]
        verbose_name = "page views for a day"
        verbose_name_plural = "page views by day"

    def __str__(self):
        return f"{self.date} {self.kind} {self.key}: {self.hits}"


class DailySearch(models.Model):
    """How often a search was made on one day and how many products it found."""

    date = models.DateField(db_index=True)
    query = models.CharField(max_length=100)
    results = models.PositiveIntegerField(default=0)
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("date", "query")]
        verbose_name = "searches for a day"
        verbose_name_plural = "searches by day"

    def __str__(self):
        return f"{self.date} {self.query!r}: {self.hits}"


class DailyVisitor(models.Model):
    """One visitor on one day, known only by a token that cannot be traced back.

    The token is a hash of the address and browser with a secret that changes
    every day, so the same person is one visitor for the day and a stranger
    again tomorrow. The country comes from the address and is all that is kept.
    ``returning`` says only that the browser had visited before: the cookie
    behind it holds a single "1" and nothing that identifies anyone.
    """

    date = models.DateField(db_index=True)
    token = models.CharField(max_length=32)
    country = models.CharField(max_length=2, blank=True)
    device = models.CharField(max_length=8, blank=True)    # mobile, tablet or desktop
    source = models.CharField(max_length=80, blank=True)   # the site that sent them, domain only; empty means typed or bookmarked
    returning = models.BooleanField(default=False)         # their browser carried the "been here before" cookie
    views = models.PositiveIntegerField(default=0)         # pages they opened that day, so a scraper can be capped

    class Meta:
        unique_together = [("date", "token")]
        verbose_name = "visitor for a day"
        verbose_name_plural = "visitors by day"

    def __str__(self):
        return f"{self.date} {self.country or '??'}"
