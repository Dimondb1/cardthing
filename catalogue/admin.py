from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html

from . import pricing
from .models import DailyLowestPrice, Game, ImportRun, Listing, OutboundClick, Product, ProductSet, Retailer


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


class ListingInline(admin.TabularInline):
    model = Listing
    extra = 0
    fields = ("retailer", "url", "price", "delivery_cost", "availability", "last_checked", "is_active")
    autocomplete_fields = ("retailer",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "game", "product_set", "product_type", "cheapest", "listing_count", "is_active")
    list_filter = ("game", "product_type", "is_active")
    search_fields = ("name", "ean", "product_set__name", "product_set__code")
    autocomplete_fields = ("product_set",)
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("image_preview", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "slug", "game", "product_set", "product_type", "is_active")}),
        ("Details", {"fields": ("image", "image_preview", "ean", "release_date")}),
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

    @admin.display(description="Current image")
    def image_preview(self, obj):
        if not obj.image:
            return "No image uploaded."
        return format_html('<img src="{}" alt="" style="max-height: 160px;">', obj.image.url)

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
                                   "from this source. Products are matched by barcode."}),
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
