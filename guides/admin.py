"""Guides in Admin, for marketing: write a guide as questions, see what still stops it being published, and see
whether readers found it useful."""

from datetime import timedelta
from urllib.parse import urlsplit

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.forms.models import BaseInlineFormSet
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from catalogue.models import Product, Retailer

from . import text
from .models import Guide, PriceCheck, Section


def shop_hosts():
    """Every shop's address, so a guide never links to one."""
    hosts = set()
    for website in Retailer.objects.values_list("website", flat=True):
        host = (urlsplit(website or "").hostname or "").lower().removeprefix("www.")
        if host:
            hosts.add(host)
    return hosts


class GuideForm(forms.ModelForm):
    class Meta:
        model = Guide
        fields = "__all__"
        widgets = {"checklist": forms.Textarea(attrs={"rows": 8})}

    def clean(self):
        cleaned = super().clean()
        found = []
        for label, value in text.guide_fields(Guide(**{name: cleaned.get(name, "") for name in (
                "title", "search_title", "one_line_answer", "door_line", "checklist_title", "checklist")})):
            found += text.style_problems(value or "", label)
        if found:
            raise ValidationError(found)
        return cleaned


class SectionForm(forms.ModelForm):
    box_type = forms.ChoiceField(label="What is in the box card", required=False,
                                 choices=[("", "None")] + [c for c in Product.Type.choices if c[0] != "other"])

    class Meta:
        model = Section
        fields = "__all__"
        widgets = {
            "short_answer": forms.Textarea(attrs={"rows": 3}),
            "body": forms.Textarea(attrs={"rows": 8}),
            "green_flags": forms.Textarea(attrs={"rows": 3}),
            "red_flags": forms.Textarea(attrs={"rows": 3}),
            "sources": forms.Textarea(attrs={"rows": 3}),
        }


class SectionFormSet(BaseInlineFormSet):
    """Checks the guide as a whole, with its questions as they are about to be saved: house style always,
    sources and review dates before it can be published (guides/text.py)."""

    def clean(self):
        super().clean()
        if any(form.errors for form in self.forms):
            return
        sections = []
        for form in self.forms:
            data = getattr(form, "cleaned_data", None)
            if not data or data.get("DELETE"):
                continue
            if not data.get("question") and not data.get("short_answer"):
                continue
            sections.append(Section(**{name: data.get(name) or "" for name in (
                "question", "short_answer", "body", "green_flags", "red_flags", "sources")},
                states_facts=bool(data.get("states_facts")), position=data.get("position") or 0))
        sections.sort(key=lambda section: section.position)
        found = text.problems(self.instance, sections, shop_hosts(), timezone.localdate())
        style = [problem for problem in found["style"] if not problem.startswith(tuple(
            label + ":" for label, _ in text.guide_fields(self.instance)))]   # the guide's own, on its form
        if style:
            raise ValidationError(style)
        if self.instance.is_published and found["publish"]:
            raise ValidationError(["Not published yet:"] + found["publish"])


class SectionInline(admin.StackedInline):
    model = Section
    form = SectionForm
    formset = SectionFormSet
    extra = 1
    autocomplete_fields = ("products",)
    fieldsets = (
        (None, {"fields": ("position", "question", "short_answer", "body")}),
        ("Signs to look for", {"fields": ("green_flags", "red_flags"), "classes": ("collapse",)}),
        ("Live prices and cards", {"fields": ("products", "box_type", "box_game", "show_checker"),
                                    "classes": ("collapse",)}),
        ("Sources", {"fields": ("states_facts", "sources")}),
    )


class DueFilter(admin.SimpleListFilter):
    title = "review"
    parameter_name = "review"

    def lookups(self, request, model_admin):
        return [("due", "Due for review")]

    def queryset(self, request, queryset):
        if self.value() == "due":
            return queryset.filter(next_review__lt=timezone.localdate())
        return queryset


class ImportForm(forms.Form):
    file = forms.FileField(label="The guide as a file", required=False,
                           help_text="A Word file (.docx) or a text file (.txt). From Google Docs: File, Download, Microsoft Word.")
    text = forms.CharField(label="Or paste the guide here", required=False, widget=forms.Textarea(attrs={"rows": 12}))


@admin.register(Guide)
class GuideAdmin(admin.ModelAdmin):
    form = GuideForm
    inlines = [SectionInline]
    change_list_template = "admin/guides_change_list.html"
    list_display = ("title", "is_published", "door", "next_review", "review_due", "useful_30")
    list_filter = ("is_published", DueFilter, "audience")
    search_fields = ("title", "sections__question")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("what_stops_it", "readers", "published_at")
    save_on_top = True
    fieldsets = (
        (None, {"fields": ("what_stops_it", "title", "slug", "one_line_answer", "audience", "is_published",
                           "position")}),
        ("Search and sharing", {"fields": ("search_title", "share_image")}),
        ("Linked from", {"fields": ("door", "door_line")}),
        ("Fact check", {"fields": ("owner", "reviewed_by", "last_reviewed", "next_review")}),
        ("Printable checklist", {"fields": ("checklist_title", "checklist"), "classes": ("collapse",)}),
        ("Readers", {"fields": ("readers", "published_at")}),
    )

    def get_queryset(self, request):
        since = timezone.localdate() - timedelta(days=30)
        return super().get_queryset(request).annotate(
            yes=Sum("votes__hits", filter=Q(votes__useful=True, votes__date__gte=since)),
            no=Sum("votes__hits", filter=Q(votes__useful=False, votes__date__gte=since)),
        )

    @admin.display(boolean=True, description="review due")
    def review_due(self, obj):
        return bool(obj.next_review and obj.next_review < timezone.localdate())

    @admin.display(description="useful, 30 days")
    def useful_30(self, obj):
        return f"{obj.yes or 0} yes, {obj.no or 0} no"

    @admin.display(description="before it can be published")
    def what_stops_it(self, obj):
        if obj.pk is None:
            return "Save the guide with its questions to see what is still needed."
        found = text.problems(obj, list(obj.sections.all()), shop_hosts(), timezone.localdate())
        rows = found["style"] + found["publish"]
        if obj.next_review and obj.next_review < timezone.localdate():
            rows.append(f"The review was due on {obj.next_review:%-d %b %Y}: check the facts and set a new date.")
        if not rows:
            return "Nothing: it can be published." if not obj.is_published else "Nothing. It is live."
        return format_html("<ul>{}</ul>", format_html_join("", "<li>{}</li>", ((row,) for row in rows)))

    @admin.display(description="was this useful?")
    def readers(self, obj):
        if obj.pk is None:
            return ""
        since = timezone.localdate() - timedelta(days=30)
        totals = obj.votes.filter(date__gte=since).values("useful").annotate(n=Sum("hits"))
        counts = {row["useful"]: row["n"] for row in totals}
        return (f"Last 30 days: {counts.get(True, 0)} yes, {counts.get(False, 0)} no. What readers wrote is in "
                f"Messages.")

    def save_model(self, request, obj, form, change):
        if obj.is_published and obj.published_at is None:
            obj.published_at = timezone.now()
        super().save_model(request, obj, form, change)

    # Import a guide: the way marketing adds and updates guides (guides/importer.py) ---------------------------

    def get_urls(self):
        from django.urls import path

        view = self.admin_site.admin_view
        return [
            path("import/", view(self.import_view), name="guides_guide_import"),
            path("import/template.docx", view(self.template_view), {"kind": "docx"}, name="guides_guide_template_docx"),
            path("import/template.txt", view(self.template_view), {"kind": "txt"}, name="guides_guide_template_txt"),
        ] + super().get_urls()

    def template_view(self, request, kind):
        from django.http import HttpResponse

        from . import template

        if kind == "docx":
            response = HttpResponse(template.docx(), content_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
            response["Content-Disposition"] = 'attachment; filename="RipRaptor guide template.docx"'
        else:
            response = HttpResponse(template.TEXT, content_type="text/plain; charset=utf-8")
            response["Content-Disposition"] = 'attachment; filename="RipRaptor guide template.txt"'
        return response

    def import_view(self, request):
        """Choose or paste a guide, see what parents will see and what is missing, then save it as a draft or
        publish it. Nothing is saved until a Save button is pressed."""
        from django.core.exceptions import PermissionDenied
        from django.http import HttpResponseRedirect
        from django.shortcuts import render
        from django.urls import reverse

        from . import importer, text as guide_text

        if not (self.has_add_permission(request) and self.has_change_permission(request)):
            raise PermissionDenied
        here = reverse("admin:guides_guide_import")
        context = {**self.admin_site.each_context(request), "opts": self.model._meta, "title": "Import a guide",
                   "form": ImportForm(), "checked": None, "saved": None}
        if request.method == "GET" and request.GET.get("saved", "").isdigit():
            saved = Guide.objects.filter(pk=request.GET["saved"]).first()
            if saved is not None:
                found = guide_text.problems(saved, list(saved.sections.all()), shop_hosts(), timezone.localdate())
                context.update(saved=saved, saved_problems=found["style"] + found["publish"])
            return render(request, "admin/guides_import.html", context)
        if request.method == "POST" and request.POST.get("action") == "publish":
            saved = Guide.objects.filter(pk=request.POST.get("guide", "0") or 0).first()
            if saved is not None:
                found = guide_text.problems(saved, list(saved.sections.all()), shop_hosts(), timezone.localdate())
                if not found["style"] and not found["publish"]:
                    saved.is_published = True
                    saved.published_at = saved.published_at or timezone.now()
                    saved.save()
                return HttpResponseRedirect(f"{here}?saved={saved.pk}")
            return HttpResponseRedirect(here)
        if request.method == "POST":
            form = ImportForm(request.POST, request.FILES)
            context["form"] = form
            lines, problem = None, ""
            if request.POST.get("action") in ("draft", "publish_now") and request.POST.get("checked_text"):
                lines = importer.text_lines(request.POST["checked_text"])
            elif form.is_valid():
                upload = form.cleaned_data.get("file")
                try:
                    if upload:
                        lines = importer.lines_from_upload(upload.name, upload.read())
                    elif form.cleaned_data.get("text", "").strip():
                        lines = importer.text_lines(form.cleaned_data["text"])
                    else:
                        problem = "Choose a file or paste the guide first."
                except ValueError as exc:
                    problem = str(exc)
            if lines is not None:
                checked = importer.check(lines)
                if request.POST.get("action") in ("draft", "publish_now") and checked.can_save:
                    saved = importer.save(checked, publish=request.POST["action"] == "publish_now")
                    return HttpResponseRedirect(f"{here}?saved={saved.pk}")
                context.update(checked=checked, checked_text="\n\n".join(lines),
                               rows=list(zip(checked.sections, checked.read.sections)))
            context["problem"] = problem
        return render(request, "admin/guides_import.html", context)


@admin.register(PriceCheck)
class PriceCheckAdmin(admin.ModelAdmin):
    """Which products people check on the Fair Price Checker and what came of it, day by day: the shortlist
    for Scalper Watch. No prices and nothing about who checked."""

    list_display = ("date", "product", "verdict", "hits")
    list_filter = ("verdict", "date")
    search_fields = ("product__name",)
    list_select_related = ("product",)
    ordering = ("-date", "-hits")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
