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
        )
        return self.annotate(
            lowest_price=Min("listings__delivered_price", filter=buyable),
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
            # Cheapest delivered price any shop has listed, whether or not it is in stock now.
            last_price=Min("listings__delivered_price", filter=live),
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
        default=0,
        help_text="Charge to deliver one item to a UK address. Used for imported prices.",
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
        FEED = "feed", "Product feed (CSV)"
        AMAZON = "amazon", "Amazon (Product Advertising API)"
        WEBSITE = "website", "Website (sitemap and product pages)"

    source_type = models.CharField(
        "price source", max_length=20, choices=Source.choices, default=Source.MANUAL
    )
    source_url = models.URLField(
        "source address",
        max_length=500,
        blank=True,
        help_text="Shopify: the shop address, for example https://shop.example/. "
        "Feed: the CSV address. Website: the shop address; product pages are found through "
        "its sitemap and read for schema.org product data. Leave empty for a local file "
        "passed to import_prices.",
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

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def delivery_for(self, price):
        """Delivery charge for one item at ``price`` under this retailer's rules."""
        if self.free_delivery_over is not None and price >= self.free_delivery_over:
            return Decimal("0.00")
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


class ListingQuerySet(models.QuerySet):
    def live(self):
        return self.filter(is_active=True, retailer__is_active=True)

    def current(self):
        return self.live().filter(last_checked__gte=stale_cutoff())

    def buyable(self):
        return self.current().filter(availability__in=Listing.BUYABLE)


class Listing(models.Model):
    """One retailer's offer for one product."""

    class Availability(models.TextChoices):
        IN_STOCK = "in_stock", "In stock"
        PREORDER = "preorder", "Pre-order"
        OUT_OF_STOCK = "out_of_stock", "Out of stock"

    BUYABLE = [Availability.IN_STOCK, Availability.PREORDER]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="listings")
    retailer = models.ForeignKey(Retailer, on_delete=models.CASCADE, related_name="listings")
    url = models.URLField("product link", max_length=1000)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    delivery_cost = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Standard delivery for one item to a UK address. 0 for free delivery.",
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
    def is_stale(self):
        return self.last_checked < stale_cutoff()

    @property
    def is_buyable(self):
        return (
            self.is_active
            and self.retailer.is_active
            and not self.is_stale
            and self.availability in self.BUYABLE
        )

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


class OutboundClick(models.Model):
    """A visit to a retailer from RipRaptor. No personal data is stored."""

    listing = models.ForeignKey(Listing, on_delete=models.SET_NULL, null=True, blank=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="outbound_clicks")
    retailer = models.ForeignKey(Retailer, on_delete=models.CASCADE, related_name="outbound_clicks")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "click to retailer"
        verbose_name_plural = "clicks to retailers"

    def __str__(self):
        return f"{self.product} to {self.retailer}"
