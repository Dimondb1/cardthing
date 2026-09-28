from django import forms
from django.contrib import admin, messages
from django.db.models import F
from django.utils.html import format_html, format_html_join
from django.utils.text import Truncator

from .formatting import check
from .models import LegalText, SiteContent
from .registry import PLACEHOLDER_HELP, REGISTRY, Kind


class SiteContentForm(forms.ModelForm):
    restore_default = forms.BooleanField(
        label="Restore default wording",
        required=False,
        help_text="Tick and save to replace the wording above with the default.",
    )

    class Meta:
        model = SiteContent
        fields = ["content", "active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = self.instance
        if instance.kind == Kind.PARAGRAPHS:
            self.fields["content"].widget = forms.Textarea(attrs={"rows": 8, "cols": 80})
            self.fields["content"].help_text = "Leave a blank line between paragraphs."
        else:
            self.fields["content"].widget = forms.TextInput(attrs={"size": 90})
        if instance.is_legal and "active" in self.fields:
            self.fields["active"].disabled = True
            self.fields["active"].help_text = "Legal text is always shown."

    def _restore_requested(self):
        return bool(self.data.get(self.add_prefix("restore_default")))

    def clean_content(self):
        if self._restore_requested():
            return self.instance.default_content
        text = self.cleaned_data.get("content", "")
        entry = REGISTRY.get(self.instance.key)
        problems = check(
            text,
            kind=self.instance.kind,
            allowed=self.instance.allowed_placeholders,
            optional=bool(entry and entry.optional),
            legal=self.instance.is_legal,
        )
        if problems:
            raise forms.ValidationError(problems)
        return text.strip()

    def clean(self):
        cleaned = super().clean()
        if self.cleaned_data.get("restore_default"):
            cleaned["active"] = True
        if self.instance.is_legal:
            cleaned["active"] = True
        return cleaned


class EditedFilter(admin.SimpleListFilter):
    title = "wording"
    parameter_name = "edited"

    def lookups(self, request, model_admin):
        return [("yes", "Edited"), ("no", "Default")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.exclude(content=F("default_content"))
        if self.value() == "no":
            return queryset.filter(content=F("default_content"))
        return queryset


@admin.register(SiteContent)
class SiteContentAdmin(admin.ModelAdmin):
    form = SiteContentForm
    list_display = ("label", "preview", "key", "edited")
    list_display_links = ("label", "key")
    list_filter = ("section", "kind", EditedFilter, "active")
    search_fields = ("key", "label", "content", "description")
    ordering = ("position",)
    list_per_page = 100
    actions = ["restore_defaults"]
    readonly_fields = (
        "label",
        "key",
        "section",
        "kind",
        "description",
        "placeholder_help",
        "default_display",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("label", "description", "key", "section")}),
        ("Wording", {"fields": ("content", "placeholder_help", "active", "restore_default")}),
        ("Default", {"fields": ("default_display", "kind", "updated_at")}),
    )

    legal = False

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_legal=self.legal)

    def has_add_permission(self, request):
        # Keys are defined in content/registry.py. A key added here would not
        # be used by any template.
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def view_on_site(self, obj):
        return obj.page_url()

    @admin.display(description="Wording")
    def preview(self, obj):
        text = obj.content if obj.active else obj.default_content
        return Truncator(text).chars(70) or "(hidden)"

    @admin.display(description="Changed")
    def edited(self, obj):
        return "Edited" if obj.is_edited else ""

    @admin.display(description="Default wording")
    def default_display(self, obj):
        return format_html(
            '<div style="white-space: pre-wrap; max-width: 60em;">{}</div>',
            obj.default_content,
        )

    @admin.display(description="Placeholders")
    def placeholder_help(self, obj):
        names = obj.allowed_placeholders
        if not names:
            return "None. This text is shown exactly as written."
        items = format_html_join(
            "",
            "<li><code>{{{}}}</code> {}</li>",
            ((name, PLACEHOLDER_HELP.get(name, "")) for name in names),
        )
        return format_html(
            "Replaced when the page is shown. Optional, but keep the curly brackets."
            '<ul style="margin: 0.5em 0 0 1.5em; padding: 0;">{}</ul>',
            items,
        )

    @admin.action(description="Restore default wording")
    def restore_defaults(self, request, queryset):
        count = 0
        for row in queryset:
            row.content = row.default_content
            row.active = True
            row.save()
            count += 1
        self.message_user(
            request,
            f"Restored the default wording for {count} item{'s' if count != 1 else ''}.",
            messages.SUCCESS,
        )


@admin.register(LegalText)
class LegalTextAdmin(SiteContentAdmin):
    legal = True
    list_filter = ("section", EditedFilter)

    def render_change_form(self, request, context, *args, **kwargs):
        context["subtitle"] = (
            "This wording tells visitors how CardScout earns money or who it is "
            "connected to. Check any change is still accurate before saving."
        )
        return super().render_change_form(request, context, *args, **kwargs)
