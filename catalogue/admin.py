from django.contrib import admin, messages
from django.db.models import Count
from django.utils.html import format_html

from . import pricing
from .models import ProductAlias, DailyLowestPrice, Game, ImportRun, Listing, OutboundClick, Product, ProductSet, Restock, Retailer, ShopProduct


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("name", "short_name", "slug", "sort_order", "is_active", "product_count")
    list_editable = ("sort_order", "is_active")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "search_aliases")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(num_products=Count("products"))

    @admin.display(description="Products", ordering="num_products")
    def product_count(self, obj):
        return obj.num_products


@admin.register(ProductSet)
class ProductSetAdmin(admin.ModelAdmin):
    list_display = ("name", "game", "code", "release_date")
    list_filter = ("game",)
    search_fields = ("name", "code")
    prepopulated_fields = {"slug": ("name",)}
    date_hierarchy = "release_date"


class BarcodeFilter(admin.SimpleListFilter):
    title = "barcode"
    parameter_name = "barcode"

    def lookups(self, request, model_admin):
        return [("missing", "Missing"), ("set", "Set")]

    def queryset(self, request, queryset):
        if self.value() == "missing":
            return queryset.filter(ean="")
        if self.value() == "set":
            return queryset.exclude(ean="")
        return queryset


class ImageFilter(admin.SimpleListFilter):
    title = "image"
    parameter_name = "image"

    def lookups(self, request, model_admin):
        return [("none", "None"), ("feed", "From a feed"), ("uploaded", "Uploaded")]

    def queryset(self, request, queryset):
        if self.value() == "none":
            return queryset.filter(image="", image_url="")
        if self.value() == "feed":
            return queryset.filter(image="").exclude(image_url="")
        if self.value() == "uploaded":
            return queryset.exclude(image="")
        return queryset


class ListingInline(admin.TabularInline):
    model = Listing
    extra = 0
    fields = ("retailer", "url", "price", "delivery_cost", "availability", "last_checked", "is_active")
    autocomplete_fields = ("retailer",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "game", "product_set", "product_type", "ean", "has_image", "cheapest", "listing_count", "is_active")
    list_filter = ("game", "product_type", "is_active", BarcodeFilter, ImageFilter)
    list_editable = ("ean",)
    search_fields = ("name", "ean", "product_set__name", "product_set__code")
    autocomplete_fields = ("product_set",)
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("image_preview", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "slug", "game", "product_set", "product_type", "is_active")}),
        ("Details", {"fields": ("image", "image_url", "image_preview", "ean", "amazon_asin", "release_date")}),
        ("Record", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )
    inlines = [ListingInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("game", "product_set").with_prices()

    @admin.display(description="Cheapest now", ordering="lowest_price")
    def cheapest(self, obj):
        return f"£{obj.lowest_price:,.2f}" if obj.lowest_price is not None else "-"

    @admin.display(description="Listings", ordering="listing_count")
    def listing_count(self, obj):
        return obj.listing_count

    @admin.display(description="Image", boolean=True)
    def has_image(self, obj):
        return bool(obj.image_src)

    @admin.display(description="Current image")
    def image_preview(self, obj):
        if not obj.image_src:
            return "No image yet. Upload one, or a price import may bring one from a retailer feed."
        source = "uploaded" if obj.image else "from a retailer feed"
        return format_html('<img src="{}" alt="" style="max-height: 160px;"><br>{}', obj.image_src, source)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        pricing.update_daily_lowest(form.instance)


@admin.register(Retailer)
class RetailerAdmin(admin.ModelAdmin):
    list_display = ("name", "website", "source_type", "delivery_cost", "free_delivery_over", "has_affiliate_link", "is_active")
    list_filter = ("is_active", "source_type")
    search_fields = ("name", "website")
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        (None, {"fields": ("name", "slug", "website", "is_active")}),
        ("Delivery", {"fields": ("delivery_cost", "free_delivery_over", "delivery_note")}),
        ("Prices", {"fields": ("source_type", "source_url"),
                    "description": "Run <code>python manage.py import_prices</code> to fetch prices "
                                   "from this source. A shop product is matched to ours by barcode, or by "
                                   "the link of a listing you add by hand under the product (any price; "
                                   "the import corrects it). Unmatched shop products are listed on each "
                                   "price import with their links."}),
        ("Links", {"fields": ("affiliate_url_template",)}),
    )

    @admin.display(description="Affiliate link", boolean=True)
    def has_affiliate_link(self, obj):
        return bool(obj.affiliate_url_template)


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ("product", "retailer", "price", "delivery_cost", "delivered_price", "availability", "last_checked", "is_active")
    list_filter = ("availability", "retailer", "is_active")
    search_fields = ("product__name", "retailer__name", "url")
    autocomplete_fields = ("product", "retailer")
    list_select_related = ("product", "retailer")
    date_hierarchy = "last_checked"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        pricing.update_daily_lowest(obj.product)


@admin.register(ImportRun)
class ImportRunAdmin(admin.ModelAdmin):
    list_display = ("retailer", "started_at", "status", "offers_found", "listings_updated", "unmatched_count")
    list_filter = ("retailer",)
    readonly_fields = ("retailer", "started_at", "finished_at", "offers_found", "listings_updated", "unmatched", "error")
    date_hierarchy = "started_at"

    @admin.display(description="Result")
    def status(self, obj):
        if obj.error:
            return "Failed"
        return "Done" if obj.finished_at else "Running"

    @admin.display(description="Unmatched")
    def unmatched_count(self, obj):
        return obj.unmatched.count("\n") + 1 if obj.unmatched else 0

    def has_add_permission(self, request):
        return False


@admin.register(ShopProduct)
class ShopProductAdmin(admin.ModelAdmin):
    list_display = ("title", "retailer", "price", "suggested", "confidence", "status", "last_seen")
    list_filter = ("status", "retailer")
    search_fields = ("title", "suggested__name")
    autocomplete_fields = ("suggested",)
    list_select_related = ("retailer", "suggested")
    readonly_fields = ("retailer", "title", "url", "price", "image_url", "confidence", "first_seen", "last_seen")
    actions = ["link_to_suggested", "mark_ignored", "mark_review"]
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    @admin.display(description="Link to our product", ordering="suggested__name")
    def suggested_name(self, obj):
        return obj.suggested

    @admin.action(description="Link to our product (creates the listing)")
    def link_to_suggested(self, request, queryset):
        made = 0
        for row in queryset.select_related("retailer", "suggested"):
            if row.suggested is None:
                continue
            listing, _created = Listing.objects.get_or_create(
                product=row.suggested, retailer=row.retailer,
                defaults={"url": row.url, "price": row.price or 0, "availability": Listing.Availability.IN_STOCK},
            )
            if listing.url != row.url:
                listing.url = row.url
                listing.save(update_fields=["url"])
            if row.image_url and not row.suggested.image_src:
                row.suggested.image_url = row.image_url
                row.suggested.save(update_fields=["image_url"])
            row.status = ShopProduct.Status.LINKED
            row.save(update_fields=["status"])
            made += 1
        self.message_user(request, f"Linked {made}. The next price import fills in the prices.", messages.SUCCESS)

    @admin.action(description="Not one of ours (hide from now on)")
    def mark_ignored(self, request, queryset):
        queryset.update(status=ShopProduct.Status.IGNORED)

    @admin.action(description="Put back for review")
    def mark_review(self, request, queryset):
        queryset.update(status=ShopProduct.Status.REVIEW)


@admin.register(DailyLowestPrice)
class DailyLowestPriceAdmin(admin.ModelAdmin):
    list_display = ("product", "date", "price")
    list_filter = ("date",)
    search_fields = ("product__name",)
    list_select_related = ("product",)
    date_hierarchy = "date"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(OutboundClick)
class OutboundClickAdmin(admin.ModelAdmin):
    list_display = ("created_at", "product", "retailer")
    list_filter = ("retailer",)
    search_fields = ("product__name",)
    list_select_related = ("product", "retailer")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Restock)
class RestockAdmin(admin.ModelAdmin):
    list_display = ("at", "product", "retailer", "price")
    list_filter = ("retailer",)
    search_fields = ("product__name",)
    list_select_related = ("product", "retailer")
    date_hierarchy = "at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ProductAlias)
class ProductAliasAdmin(admin.ModelAdmin):
    list_display = ("slug", "product", "created_at")
    search_fields = ("slug", "product__name")
    autocomplete_fields = ("product",)
