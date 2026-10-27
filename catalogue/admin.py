import re

from django import forms
from django.contrib import admin, messages
from django.db import models
from django.db.models import Count
from django.utils import timezone
from django.utils.html import format_html

from . import pricing, sanity
from .models import (
    AffiliateSale, CardmarketMatch, CardmarketProduct, ClaudeAsk, DiscordChannel, DiscordPost, DropAlert, KeyringClaim, SearchAlert, SearchAlertSent, ProductAlias, Referral, ReferralSettings, Referrer, FeaturedSettings, DailyLowestPrice, Game, ImportRun, Listing, OutboundClick, Product, ProductSet, Release,
    RemovedProduct, RemovedShop, Restock, Retailer, ShopProduct, StockAlert, TypeBand,
)

# A listing field the product page shows. Changing one here stamps the listing's changed_at, the sitemap's
# record of when the page last changed.
SHOWN_LISTING_FIELDS = {"product", "retailer", "url", "price", "delivery_cost", "delivery_known", "availability", "is_active"}


def stamp_shown_change(form):
    """Stamp a listing form's instance as changed when it is new, hidden or shown, or when a field the page
    shows was changed on a listing the page shows."""
    changed = set(form.changed_data)
    if form.instance.pk is None or "is_active" in changed or (form.instance.is_active and SHOWN_LISTING_FIELDS & changed):
        form.instance.changed_at = timezone.now()


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
    list_display = ("name", "game", "code", "release_date", "release_date_source", "not_in_shops")
    list_editable = ("not_in_shops",)
    list_filter = ("game", "not_in_shops")
    search_fields = ("name", "code")
    prepopulated_fields = {"slug": ("name",)}
    date_hierarchy = "release_date"
    readonly_fields = ("release_date_source",)

    def save_model(self, request, obj, form, change):
        if "release_date" in form.changed_data:
            # A date typed here is the owner's, and no release source changes it. Clearing it lets one again.
            from .releases import OWNER

            obj.release_date_source = OWNER if obj.release_date else ""
        super().save_model(request, obj, form, change)


@admin.register(Release)
class ReleaseAdmin(admin.ModelAdmin):
    """What each release source said. Read only: sets are added from the Things to check page."""

    list_display = ("name", "game", "code", "release_date", "precision", "source", "status", "product_set", "last_seen_at")
    list_filter = ("status", "game", "source", "official")
    search_fields = ("name", "code", "note")
    list_select_related = ("game", "product_set")
    actions = ["hide"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.action(description="Hide: not a set")
    def hide(self, request, queryset):
        count = queryset.exclude(status=Release.Status.DISMISSED).update(status=Release.Status.DISMISSED)
        self.message_user(request, f"{count} hidden. They are not suggested again from their source.", messages.SUCCESS)


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
    fields = ("retailer", "url", "price", "delivery_cost", "delivery_known", "availability", "last_checked", "is_active")
    autocomplete_fields = ("retailer",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "game", "product_set", "product_type", "ean", "has_image", "cheapest", "listing_count", "is_active")
    list_filter = ("game", "product_type", "is_active", BarcodeFilter, ImageFilter)
    list_editable = ("ean",)
    search_fields = ("name", "ean", "product_set__name", "product_set__code")
    autocomplete_fields = ("product_set",)
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("image_preview", "created_at", "updated_at", "shops_rrp")
    fieldsets = (
        (None, {"fields": ("name", "slug", "game", "product_set", "product_type", "is_active")}),
        ("Details", {"fields": ("image", "image_url", "image_preview", "ean", "amazon_asin", "release_date")}),
        ("RRP", {"fields": ("rrp", "shops_rrp")}),
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

    @admin.display(description="RRP UK shops give")
    def shops_rrp(self, obj):
        """The RRP at least two UK shops give beside their price, as kept each hour; the RRP above wins over it."""
        if not obj.shop_rrp:
            return "None yet: fewer than two UK shops give the same RRP."
        return f"£{obj.shop_rrp:.2f}, given by {obj.shop_rrp_shops} UK shops"

    @admin.display(description="Current image")
    def image_preview(self, obj):
        if not obj.image_src:
            return "No image yet. Upload one, or a price import may bring one from a retailer feed."
        source = "uploaded" if obj.image else "from a retailer feed"
        return format_html('<img src="{}" alt="" style="max-height: 160px;"><br>{}', obj.image_src, source)

    def save_model(self, request, obj, form, change):
        old_slug = form.initial.get("slug") if change and "slug" in form.changed_data else None
        super().save_model(request, obj, form, change)
        if old_slug and old_slug != obj.slug:
            # A new address: the old one keeps working, as after a merge, so links and search results follow.
            ProductAlias.objects.update_or_create(slug=old_slug, defaults={"product": obj})
            ProductAlias.objects.filter(slug=obj.slug, product=obj).delete()

    def save_formset(self, request, form, formset, change):
        if formset.model is Listing:
            for listing_form in formset.forms:
                if listing_form.has_changed():
                    stamp_shown_change(listing_form)
        super().save_formset(request, form, formset, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        pricing.update_daily_lowest(form.instance)


@admin.register(Retailer)
class RetailerAdmin(admin.ModelAdmin):
    list_display = ("name", "website", "source_type", "delivery_cost", "free_delivery_over", "has_affiliate_link",
                    "is_active", "read_every_minutes")
    # How often each shop is read can be changed straight from the list. The rest of the reading record is
    # written by the reads themselves.
    list_editable = ("read_every_minutes",)
    readonly_fields = ("next_read_at", "last_read_seconds", "last_ok_at", "last_error", "error_streak",
                       "backoff_until", "reading_paused", "collections_polled_at", "collections_ok")
    list_filter = ("is_active", "source_type")
    search_fields = ("name", "website")
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        (None, {"fields": ("name", "slug", "website", "is_active")}),
        ("Delivery", {"fields": ("delivery_cost", "delivery_cost_up_to", "free_delivery_over", "delivery_note")}),
        ("Prices", {"fields": ("source_type", "source_url", "collection", "default_game", "extra_products", "session_url"),
                    "description": "Run <code>python manage.py import_prices</code> to fetch prices "
                                   "from this source. A shop product is matched to ours by barcode, or by "
                                   "the link of a listing you add by hand under the product (any price; "
                                   "the import corrects it). Unmatched shop products are listed on each "
                                   "price import with their links."}),
        ("Reading", {"fields": ("read_every_minutes", "next_read_at", "last_read_seconds", "last_ok_at",
                                "error_streak", "backoff_until", "last_error", "reading_paused",
                                "collections_polled_at", "collections_ok")}),
        ("Links", {"fields": ("affiliate_url_template",)}),
    )

    @admin.display(description="Affiliate link", boolean=True)
    def has_affiliate_link(self, obj):
        return bool(obj.affiliate_url_template)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # New delivery rules apply to the shop's prices at once, not at its next import.
        if change and {"delivery_cost", "delivery_cost_up_to", "free_delivery_over"} & set(form.changed_data):
            pricing.apply_delivery_rules(obj)
        if change and "is_active" in form.changed_data:
            # The shop leaves or joins every page it is listed on: those pages changed (the sitemap's lastmod).
            Listing.objects.filter(retailer=obj, is_active=True).update(changed_at=timezone.now())

    def delete_model(self, request, obj):
        # Deleting a shop takes its prices and its reading with it, and every update leaves it deleted.
        RemovedShop.record(obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for shop in queryset:
            RemovedShop.record(shop)
        super().delete_queryset(request, queryset)


@admin.register(RemovedShop)
class RemovedShopAdmin(admin.ModelAdmin):
    """Shops deleted under Retailers, which updates never add back. Delete one to let the next update add it again
    if it is in the shop list; adding the shop under Retailers does the same."""

    list_display = ("name", "slug", "host", "removed_at")
    search_fields = ("name", "slug", "host")
    readonly_fields = ("name", "slug", "host", "removed_at")

    def has_add_permission(self, request):
        return False


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ("product", "retailer", "price", "delivery_cost", "delivery_known", "delivered_price", "availability", "sanity", "last_checked", "is_active")
    list_filter = ("availability", "sanity", "retailer", "is_active")
    search_fields = ("product__name", "retailer__name", "url")
    autocomplete_fields = ("product", "retailer")
    list_select_related = ("product", "retailer")
    date_hierarchy = "last_checked"
    # Verdicts are worked out from the other shops' prices; the owner acts on them from Things to check.
    readonly_fields = ("sanity", "sanity_reason", "sanity_ratio", "sanity_at", "last_ok_price", "trusted_price", "trusted_at",
                       "shop_published_at", "first_preorder_at", "deal_quantity", "deal_price", "changed_at")

    def save_model(self, request, obj, form, change):
        stamp_shown_change(form)
        super().save_model(request, obj, form, change)
        # A price, stock or shop typed in here is judged like any check, and so is the product it left.
        moved_from = form.initial.get("product") if change and "product" in form.changed_data else None
        for product_id in {obj.product_id, moved_from} - {None}:
            sanity.judge_product(product_id)
        pricing.update_daily_lowest(obj.product)


@admin.register(ImportRun)
class ImportRunAdmin(admin.ModelAdmin):
    list_display = ("retailer", "started_at", "status", "offers_found", "listings_updated", "unmatched_count")
    list_filter = ("retailer",)
    readonly_fields = ("retailer", "note", "started_at", "finished_at", "offers_found", "listings_updated", "error",
                       "what_happened", "unmatched")
    exclude = ("log",)
    date_hierarchy = "started_at"

    @admin.display(description="What happened")
    def what_happened(self, obj):
        from django.utils.html import format_html

        if not obj.log:
            return "Nothing logged. A read keeps its log only when it fails or warns, for a week."
        return format_html(
            '<pre style="white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; font-size: 12px; line-height: 1.5;">{}</pre>',
            obj.log,
        )

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
    list_display = ("title", "retailer", "price", "suggested", "confidence", "status", "source", "last_seen")
    list_filter = ("status", "source", "retailer")
    search_fields = ("title", "suggested__name")
    autocomplete_fields = ("suggested",)
    list_select_related = ("retailer", "suggested")
    readonly_fields = ("retailer", "title", "url", "price", "availability", "image_url", "confidence", "source", "product", "first_seen",
                       "last_seen")
    actions = ["link_to_suggested", "mark_ignored", "mark_review"]
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    @admin.display(description="Link to our product", ordering="suggested__name")
    def suggested_name(self, obj):
        return obj.suggested

    @admin.action(description="Link to our product (creates the listing)")
    def link_to_suggested(self, request, queryset):
        from . import finder

        made = 0
        for row in queryset.select_related("retailer", "suggested"):
            if finder.link(row) is not None:
                made += 1
        self.message_user(request, f"Linked {made}. The next price import fills in the prices.", messages.SUCCESS)

    @admin.action(description="Not one of ours (hide from now on)")
    def mark_ignored(self, request, queryset):
        from . import finder

        for row in queryset.select_related("retailer"):
            finder.ignore(row)

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


@admin.register(TypeBand)
class TypeBandAdmin(admin.ModelAdmin):
    """Worked out each night by snapshot_daily_prices (or price_bands); nothing here is edited by hand."""

    list_display = ("game", "product_type", "product_set", "n", "p10", "median", "p90", "computed_at")
    list_filter = ("game", "product_type")
    list_select_related = ("game", "product_set")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(OutboundClick)
class OutboundClickAdmin(admin.ModelAdmin):
    list_display = ("created_at", "product", "retailer", "source", "button", "suspect")
    list_filter = ("suspect", "button", "source", "retailer")
    search_fields = ("product__name",)
    list_select_related = ("product", "retailer")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CardmarketProduct)
class CardmarketProductAdmin(admin.ModelAdmin):
    """Cardmarket's sealed products, as its daily catalogue lists them (catalogue/cardmarket.py). Read only."""

    list_display = ("name", "category", "game", "latest_trend", "added_on")
    list_filter = ("game", "category")
    search_fields = ("name", "=id")
    list_select_related = ("game",)

    @admin.display(description="Trend (€)")
    def latest_trend(self, obj):
        price = obj.prices.order_by("-date").first()
        return price.trend if price else None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CardmarketMatch)
class CardmarketMatchAdmin(admin.ModelAdmin):
    """Which Cardmarket product each of ours is, as Claude matched it (catalogue/cardmarket_match.py). Only a
    match Claude was sure of, or one you confirm, is used. Claude never asks again about one you answered."""

    list_display = ("product", "cardmarket", "status", "confidence", "reason", "decided_at")
    list_filter = ("status", "product__game")
    search_fields = ("product__name", "cardmarket__name")
    raw_id_fields = ("product", "cardmarket")
    list_select_related = ("product", "cardmarket")
    actions = ("confirm", "reject")

    @admin.action(description="Confirm: this is the same product")
    def confirm(self, request, queryset):
        done = queryset.filter(cardmarket__isnull=False).update(status=CardmarketMatch.Status.CONFIRMED,
                                                                 decided_at=timezone.now())
        self.message_user(request, f"{done} confirmed. Rows with no Cardmarket product were left as they were.")

    @admin.action(description="Reject: not the same product")
    def reject(self, request, queryset):
        done = queryset.update(status=CardmarketMatch.Status.REJECTED, cardmarket=None, decided_at=timezone.now())
        self.message_user(request, f"{done} rejected. Claude will not ask about them again.")


class SalesImportForm(forms.Form):
    file = forms.FileField(label="Network report (CSV)")
    retailer = forms.ModelChoiceField(
        queryset=Retailer.objects.order_by("name"), required=False, label="Shop, when the file has no shop column",
        help_text="eBay's and Amazon's own reports name no shop: choose eBay or Amazon here.",
    )


@admin.register(AffiliateSale)
class AffiliateSaleAdmin(admin.ModelAdmin):
    """Sales the affiliate programmes reported: what clicks earned. Imported from a network's CSV report, or
    entered by hand. The dashboard reads them for commission per 100 clicks, conversion and revenue."""

    list_display = ("ordered_on", "retailer", "game", "status", "commission", "order_value", "click")
    list_filter = ("status", "retailer", "game")
    date_hierarchy = "ordered_on"
    raw_id_fields = ("click",)
    search_fields = ("reference", "note")
    list_select_related = ("retailer", "game", "click")
    change_list_template = "admin/sales_change_list.html"

    def get_urls(self):
        from django.urls import path

        return [path("import/", self.admin_site.admin_view(self.import_view), name="catalogue_affiliatesale_import")
                ] + super().get_urls()

    def import_view(self, request):
        from django.http import HttpResponseRedirect
        from django.shortcuts import render
        from django.urls import reverse

        from .sales import import_report

        if not self.has_add_permission(request):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        form = SalesImportForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            result = import_report(form.cleaned_data["file"].read(), default_retailer=form.cleaned_data["retailer"])
            messages.success(request, f"{result.added} sales added, {result.updated} updated.")
            for line, reason in result.skipped[:20]:
                messages.warning(request, f"Line {line} skipped: {reason}")
            if len(result.skipped) > 20:
                messages.warning(request, f"{len(result.skipped) - 20} more lines skipped.")
            return HttpResponseRedirect(reverse("admin:catalogue_affiliatesale_changelist"))
        context = {**self.admin_site.each_context(request), "form": form, "opts": self.model._meta,
                   "title": "Import a network report"}
        return render(request, "admin/sales_import.html", context)


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


@admin.register(StockAlert)
class StockAlertAdmin(admin.ModelAdmin):
    list_display = ("product", "email", "max_price", "watch_days", "created_at", "confirmed_at")
    list_filter = ("confirmed_at",)
    search_fields = ("product__name", "email")
    list_select_related = ("product",)
    readonly_fields = ("product", "email", "token", "created_at", "confirmed_at", "max_price", "watch_days")

    def has_add_permission(self, request):
        return False


@admin.register(DropAlert)
class DropAlertAdmin(admin.ModelAdmin):
    list_display = ("product_set", "email", "created_at", "confirmed_at", "preorder_sent_at", "eve_sent_at")
    list_filter = ("confirmed_at",)
    search_fields = ("product_set__name", "email")
    list_select_related = ("product_set",)
    readonly_fields = ("product_set", "email", "token", "created_at", "confirmed_at", "confirm_sent_at",
                       "preorder_sent_at", "eve_sent_at", "eve_for", "day_sent_at")

    def has_add_permission(self, request):
        return False


class SearchAlertSentInline(admin.TabularInline):
    model = SearchAlertSent
    fields = ("product", "sent_at", "gone_at")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SearchAlert)
class SearchAlertAdmin(admin.ModelAdmin):
    """The watchlist page's search alerts. Read only: each belongs to the visitor who asked for it, and its
    email's stop link deletes it. Deleting one here deletes it at once too."""

    list_display = ("game", "product_type", "language", "max_price", "email", "created_at", "confirmed_at", "sent_at")
    list_filter = ("confirmed_at", "game")
    search_fields = ("email",)
    list_select_related = ("game",)
    readonly_fields = ("email", "token", "game", "product_type", "language", "max_price", "created_at",
                       "confirmed_at", "confirm_sent_at", "sent_at")
    inlines = [SearchAlertSentInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ProductAlias)
class ProductAliasAdmin(admin.ModelAdmin):
    list_display = ("slug", "product", "created_at")
    search_fields = ("slug", "product__name")
    autocomplete_fields = ("product",)


@admin.register(RemovedProduct)
class RemovedProductAdmin(admin.ModelAdmin):
    """Addresses of products tidy_catalogue took down. Each answers 410 Gone with links to its set and game for a
    year. Delete one to make its address an ordinary 404."""

    list_display = ("slug", "name", "game", "product_set", "removed_at")
    list_filter = ("game",)
    search_fields = ("slug", "name")
    list_select_related = ("game", "product_set")
    readonly_fields = ("slug", "name", "game", "product_set", "removed_at")

    def has_add_permission(self, request):
        return False


@admin.register(ClaudeAsk)
class ClaudeAskAdmin(admin.ModelAdmin):
    """What was asked of Claude, what it answered and what it cost. Read only: the judge writes it."""

    list_display = ("asked_at", "row_key", "outcome", "verdict", "confidence", "action", "owner_answer", "cost")
    list_filter = ("kind", "outcome", "verdict", "action")
    date_hierarchy = "asked_at"

    @admin.display(description="Cost")
    def cost(self, obj):
        from .judge import dollars

        return dollars(obj.cost_micros + obj.reserved_micros)

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# Refer a friend ----------------------------------------------------------------------------------

@admin.register(ReferralSettings)
class ReferralSettingsAdmin(admin.ModelAdmin):
    """The one row of owner controls. The list goes straight to it."""

    fields = ("is_on", "keyrings_left", "ends_on", "max_per_referrer", "summary")
    readonly_fields = ("summary",)

    def has_add_permission(self, request):
        return False   # the list opens the one row, made on first use; checking here would cost every admin page a query

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from django.http import HttpResponseRedirect
        from django.urls import reverse

        row = ReferralSettings.load()
        return HttpResponseRedirect(reverse("admin:catalogue_referralsettings_change", args=[row.pk]))

    @admin.display(description="Now")
    def summary(self, obj):
        from . import referrals

        counts = Referral.objects.aggregate(
            review=Count("pk", filter=models.Q(status=Referral.Status.REVIEW)),
            complete=Count("pk", filter=models.Q(status=Referral.Status.COMPLETE)),
        )
        to_post = referrals.to_post(KeyringClaim.objects.all()).count()
        state = "open to new people" if referrals.taking_part(obj) else "closed to new people"
        return (f"The offer is {state}. Referrals for review: {counts['review']}. Complete: {counts['complete']}. "
                f"Keyrings to post: {to_post}.")

    def save_model(self, request, obj, form, change):
        from . import referrals

        super().save_model(request, obj, form, change)
        referrals.refresh_flag(obj)


@admin.register(FeaturedSettings)
class FeaturedSettingsAdmin(admin.ModelAdmin):
    """The one row for the home page's Featured deals. The list goes straight to it."""

    fields = ("paying_only", "paying_shops")
    readonly_fields = ("paying_shops",)

    def has_add_permission(self, request):
        return False   # the list opens the one row, made on first use

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from django.http import HttpResponseRedirect
        from django.urls import reverse

        row = FeaturedSettings.load()
        return HttpResponseRedirect(reverse("admin:catalogue_featuredsettings_change", args=[row.pk]))

    @admin.display(description="Shops that count as paying")
    def paying_shops(self, obj):
        """The shops whose deals count as paying now: an affiliate link format on the retailer, or eBay or Amazon."""
        names = list(Retailer.objects.filter(is_active=True).filter(
            ~models.Q(affiliate_url_template="")
            | models.Q(source_type__in=[Retailer.Source.EBAY, Retailer.Source.AMAZON])
        ).order_by("name").values_list("name", flat=True))
        if not names:
            return "None yet. Add a shop's affiliate link format under Retailers and it counts from then on."
        return ", ".join(names) + ". Add a shop's affiliate link format under Retailers to add it."

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from .signals import clear_list_caches

        clear_list_caches(force=True)   # the home page's cached row is built again with the new choice


# A Discord webhook's address: https://discord.com/api/webhooks/<id>/<token> (older ones say discordapp.com).
DISCORD_WEBHOOK = re.compile(r"^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$")


class DiscordChannelForm(forms.ModelForm):
    """The webhook address is typed as a password: never shown again once saved, and an empty box keeps it."""

    webhook_url = forms.CharField(
        label="Webhook address", required=False, widget=forms.PasswordInput(render_value=False),
        help_text="In Discord: Edit Channel, Integrations, Webhooks, New Webhook, Copy Webhook URL. Paste it here. "
                  "Leave empty to keep the one saved.",
    )
    send_test = forms.BooleanField(
        label="Send a test post", required=False,
        help_text="Tick and save to post one hello to this channel, with the name and picture above.",
    )

    class Meta:
        model = DiscordChannel
        fields = "__all__"
        widgets = {"games": forms.CheckboxSelectMultiple}   # tick boxes: a phone can use them

    def clean_webhook_url(self):
        typed = self.cleaned_data.get("webhook_url", "").strip()
        if not typed:
            if not self.instance.webhook_url:
                raise forms.ValidationError("Paste the channel's webhook address from Discord.")
            return self.instance.webhook_url
        if not DISCORD_WEBHOOK.match(typed):
            raise forms.ValidationError("That is not a Discord webhook address. It starts https://discord.com/api/webhooks/")
        return typed

    def clean(self):
        cleaned = super().clean()
        if not any(cleaned.get(name) for name in ("restocks", "preorders", "price_drops", "drop_days")):
            raise forms.ValidationError("Tick at least one update for this channel.")
        return cleaned


@admin.register(DiscordChannel)
class DiscordChannelAdmin(admin.ModelAdmin):
    """The channels RipRaptor posts to on the owner's Discord (catalogue/discord.py), one page each: its webhook,
    which updates and games, how often, and how its posts look."""

    form = DiscordChannelForm
    list_display = ("label", "updates", "every_minutes", "is_active", "last_post")
    list_filter = ("is_active",)
    fieldsets = (
        (None, {"fields": ("label", "webhook_url", "webhook_saved", "is_active")}),
        ("Updates for this channel", {"fields": ("restocks", "preorders", "price_drops", "drop_days", "games", "sets",
                                                 "min_drop_percent")}),
        ("How often", {"fields": ("every_minutes", "per_post")}),
        ("How it looks", {"fields": ("username", "use_logo", "send_test")}),
    )
    readonly_fields = ("webhook_saved",)
    autocomplete_fields = ("sets",)   # type a set's name: hundreds of sets are too many to tick

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("sets")   # the list names each channel's sets

    @admin.display(description="Saved webhook")
    def webhook_saved(self, obj):
        return obj.webhook_hint if obj and obj.pk else "not set"

    @admin.display(description="Updates")
    def updates(self, obj):
        names = [label for field, label in (("restocks", "Back in stock"), ("preorders", "Pre-orders"),
                                            ("price_drops", "Price drops"), ("drop_days", "Out today"))
                 if getattr(obj, field)]
        sets = [product_set.name for product_set in obj.sets.all()]
        return (", ".join(names) or "None") + (f" ({', '.join(sets)} only)" if sets else "")

    @admin.display(description="Last post")
    def last_post(self, obj):
        from . import discord

        last = obj.posts.exclude(kind=discord.NOTED).order_by("-posted_at").values_list("posted_at", flat=True).first()
        return timezone.localtime(last).strftime("%d %b %H:%M") if last else "Nothing yet"

    def save_model(self, request, obj, form, change):
        from . import discord

        super().save_model(request, obj, form, change)
        if form.cleaned_data.get("send_test"):
            try:
                discord.test(channels_to=[obj])
            except discord.DiscordError as exc:
                self.message_user(request, f"Test not posted: {exc}. Check the webhook address.", messages.ERROR)
            else:
                self.message_user(request, f"Test posted to {obj.label}.")


@admin.register(DiscordPost)
class DiscordPostAdmin(admin.ModelAdmin):
    """What has been posted to Discord, newest first, so each piece of news goes once. Read only."""

    list_display = ("key", "kind", "posted_at")
    list_filter = ("kind",)
    search_fields = ("key",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Referrer)
class ReferrerAdmin(admin.ModelAdmin):
    list_display = ("email", "created_at", "confirmed_at")
    list_filter = ("confirmed_at",)
    search_fields = ("email",)
    readonly_fields = ("email", "email_key", "code", "token", "connection", "network", "created_at",
                       "confirm_sent_at", "confirmed_at")

    def has_add_permission(self, request):
        return False


class ClaimInline(admin.TabularInline):
    model = KeyringClaim
    fields = ("role", "submitted_at", "posted_at")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ("__str__", "status", "review_reason", "created_at", "completed_at", "emails_sent_at")
    list_filter = ("status",)
    list_select_related = ("referrer",)
    readonly_fields = ("referrer", "friend_email", "friend_key", "token", "connection", "network", "status",
                       "review_reason", "created_at", "confirm_sent_at", "completed_at", "emails_sent_at", "cancelled_at")
    inlines = [ClaimInline]
    actions = ["approve", "cancel"]

    def has_add_permission(self, request):
        return False

    # Deleting puts any keyring still promised back in stock, as Cancel does.
    def delete_model(self, request, obj):
        from . import referrals

        referrals.delete_referrals(Referral.objects.filter(pk=obj.pk))

    def delete_queryset(self, request, queryset):
        from . import referrals

        referrals.delete_referrals(queryset)

    @admin.action(description="Approve: email both people for their address")
    def approve(self, request, queryset):
        from . import referrals

        done = sum(referrals.approve(referral) for referral in queryset.filter(status=Referral.Status.REVIEW))
        self.message_user(request, f"Approved {done}. Referrals not held for review were left alone.")

    @admin.action(description="Cancel: no more emails, keyrings back to stock")
    def cancel(self, request, queryset):
        from . import referrals

        done = sum(referrals.cancel(referral) for referral in queryset)
        self.message_user(request, f"Cancelled {done}. Nobody was emailed.", messages.WARNING)


class ToPostFilter(admin.SimpleListFilter):
    title = "state"
    parameter_name = "state"

    def lookups(self, request, model_admin):
        return [("to_post", "To post"), ("waiting", "Address not sent yet"), ("posted", "Posted")]

    def queryset(self, request, queryset):
        from . import referrals

        if self.value() == "to_post":
            return referrals.to_post(queryset)
        if self.value() == "waiting":
            return queryset.filter(submitted_at__isnull=True)
        if self.value() == "posted":
            return queryset.filter(posted_at__isnull=False)
        return queryset


@admin.register(KeyringClaim)
class KeyringClaimAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "check_first", "role", "status", "submitted_at", "posted_at")
    list_filter = (ToPostFilter, "same_postcode")
    search_fields = ("name", "postcode")
    list_select_related = ("referral",)
    readonly_fields = ("referral", "role", "token", "created_at", "name", "line1", "line2", "town", "postcode",
                       "address_key", "same_postcode", "submitted_at", "posted_at")
    actions = ["mark_posted"]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Address")
    def address(self, obj):
        """Every line, ready to copy onto an envelope."""
        return ", ".join(part for part in (obj.line1, obj.line2, obj.town, obj.postcode) if part)

    @admin.display(description="Check first", boolean=True, ordering="same_postcode")
    def check_first(self, obj):
        return obj.same_postcode

    @admin.display(description="Referral", ordering="referral__status")
    def status(self, obj):
        return obj.referral.get_status_display()

    # Deleting puts a keyring still promised back in stock, so Keyrings left matches the drawer.
    def delete_model(self, request, obj):
        from . import referrals

        referrals.delete_claims(KeyringClaim.objects.filter(pk=obj.pk))

    def delete_queryset(self, request, queryset):
        from . import referrals

        referrals.delete_claims(queryset)

    @admin.action(description="Mark as posted")
    def mark_posted(self, request, queryset):
        from . import referrals

        done = referrals.mark_posted(queryset)
        self.message_user(request, f"Marked {done} as posted. Their addresses are deleted 60 days from now. "
                                   f"Addresses not sent yet, already posted or of a cancelled referral were left alone.")
