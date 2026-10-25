from django import forms
from django.contrib import admin, messages

from catalogue import mail

from . import service
from .models import Conversation, ShopContact, ShopEnquiry


class ConversationForm(forms.ModelForm):
    reply = forms.CharField(
        label="Your reply", required=False, max_length=service.MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 6, "cols": 80}),
        help_text="Shown on their private conversation page. If they left an email address, it is emailed to them too.",
    )

    class Meta:
        model = Conversation
        fields = ("closed",)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    form = ConversationForm
    list_display = ("summary", "name", "email", "last_message_at", "unread", "closed")
    list_filter = ("unread", "closed")
    search_fields = ("name", "email", "messages__body")
    readonly_fields = ("thread", "name", "email", "created_at", "last_message_at", "visitor_link")
    fieldsets = (
        (None, {"fields": ("thread", "reply")}),
        ("Visitor", {"fields": ("name", "email", "created_at", "last_message_at", "visitor_link", "closed")}),
    )

    @admin.display(description="First message")
    def summary(self, obj):
        return str(obj)

    @admin.display(description="Messages")
    def thread(self, obj):
        from django.template.defaultfilters import linebreaksbr
        from django.utils import timezone
        from django.utils.html import format_html, format_html_join

        rows = format_html_join(
            "", '<div style="margin:0 0 10px;padding:10px 14px;border-radius:10px;max-width:46rem;{}"><div style="font-size:12px;color:var(--body-quiet-color);margin-bottom:4px"><strong>{}</strong> {}</div>{}</div>',
            # Admin's own colour variables, so the thread reads in its light and dark themes alike.
            ((("background:var(--darkened-bg);color:var(--body-fg);border:1px solid var(--hairline-color);" if m.from_visitor
               else "background:var(--selected-bg);color:var(--body-fg);border:1px solid var(--primary);"),
              "Visitor" if m.from_visitor else "You", timezone.localtime(m.created_at).strftime("%-d %b %Y, %H:%M"), linebreaksbr(m.body, autoescape=True))
             for m in obj.messages.all()),
        )
        return format_html('<div style="width:100%">{}</div>', rows)

    @admin.display(description="Their page")
    def visitor_link(self, obj):
        from django.utils.html import format_html

        return format_html('<a href="{}" target="_blank" rel="noopener">Open what they see</a>', obj.get_absolute_url())

    def has_add_permission(self, request):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        if request.method == "GET":
            Conversation.objects.filter(pk=object_id).update(unread=False)
        return super().change_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        body = form.cleaned_data.get("reply", "").strip()
        if not body:
            return
        try:
            emailed = service.reply(obj, body)
        except mail.MailError as exc:
            self.message_user(request, f"Reply saved on their page, but the email failed: {exc}", messages.WARNING)
            return
        if emailed:
            self.message_user(request, f"Reply saved and emailed to {obj.email}.")
        else:
            self.message_user(request, "Reply saved. They left no email address, so they will see it when they open their link.")

    def response_change(self, request, obj):
        if "_continue" not in request.POST and request.POST.get("reply", "").strip():
            from django.http import HttpResponseRedirect

            return HttpResponseRedirect(request.path)
        return super().response_change(request, obj)


class HandledFilter(admin.SimpleListFilter):
    title = "handled"
    parameter_name = "handled"

    def lookups(self, request, model_admin):
        return (("no", "Waiting"), ("yes", "Handled"))

    def queryset(self, request, queryset):
        if self.value() == "no":
            return queryset.filter(handled_at__isnull=True)
        if self.value() == "yes":
            return queryset.filter(handled_at__isnull=False)
        return queryset


@admin.register(ShopEnquiry)
class ShopEnquiryAdmin(admin.ModelAdmin):
    """Shops asking to be listed. Answer from your own email; nothing is sent from here. The website is shown
    as text: it is what they typed, and the site never reads it until you add the shop."""

    list_display = ("name", "domain", "email", "created_at", "handled")
    list_filter = (HandledFilter,)
    search_fields = ("name", "domain", "shop_url", "email", "message", "note")
    date_hierarchy = "created_at"
    readonly_fields = ("name", "shop_url", "domain", "email", "message", "created_at")
    fields = ("name", "shop_url", "domain", "email", "message", "created_at", "handled_at", "note")
    actions = ("mark_handled",)

    @admin.display(boolean=True, description="Handled", ordering="handled_at")
    def handled(self, obj):
        return obj.handled_at is not None

    @admin.action(description="Mark handled")
    def mark_handled(self, request, queryset):
        from django.utils import timezone

        done = queryset.filter(handled_at__isnull=True).update(handled_at=timezone.now())
        self.message_user(request, f"{done} shop enquir{'y' if done == 1 else 'ies'} marked handled. "
                                   "Each is deleted a year from now.")

    def has_add_permission(self, request):
        return False


class FollowUpFilter(admin.SimpleListFilter):
    title = "follow-up"
    parameter_name = "follow_up"

    def lookups(self, request, model_admin):
        return (("due", "Due, no reply yet"),)

    def queryset(self, request, queryset):
        from . import outreach

        if self.value() == "due":
            return outreach.due(queryset)
        return queryset


@admin.register(ShopContact)
class ShopContactAdmin(admin.ModelAdmin):
    """Shop outreach: write to a shop from your own mailbox with its own figures (inbox/outreach.py). Every shop on
    the site has a row, made when this page opens. Open a draft, send it from your mail app, then press Mark as sent
    so the row shows when you wrote and when to follow up. Replies come to your own mailbox."""

    # What matters on a phone first: who sends the most shoppers, where things stand and when to write again.
    list_display = ("retailer", "visitors_quarter", "status", "follow_up_on", "last_emailed_at", "listed", "has_email",
                    "affiliate")
    list_filter = ("status", FollowUpFilter)
    search_fields = ("retailer__name", "email", "notes")
    list_select_related = ("retailer",)
    readonly_fields = ("retailer", "figures", "drafts", "sent")
    # Save comes first in the form, so Enter in a box saves rather than pressing the first Mark as sent.
    save_on_top = True
    fieldsets = (
        (None, {"fields": ("retailer", "email", "status", "follow_up_on", "notes")}),
        ("Their figures on RipRaptor", {"fields": ("figures",)}),
        ("Write to them", {"fields": ("drafts",)}),
        ("Emails you sent", {"fields": ("sent",)}),
    )

    def get_ordering(self, request):
        # The shops worth writing to first. The count is an annotation, so not in ``ordering``.
        return ("-visitors_quarter", "retailer__name")

    def get_queryset(self, request):
        from . import outreach

        # As ModelAdmin's own, with the counts added before the ordering that needs them.
        return outreach.with_counts(self.model._default_manager.get_queryset()).order_by(*self.get_ordering(request))

    def changelist_view(self, request, extra_context=None):
        from . import outreach

        outreach.ensure_contacts()
        return super().changelist_view(request, extra_context)

    def save_model(self, request, obj, form, change):
        """Saves the row, then, for Mark as sent under a draft, records the email and sets the follow-up. The button
        is part of this form, so an address typed in above is kept too."""
        from . import outreach

        super().save_model(request, obj, form, change)
        kind = request.POST.get("_sent", "")
        if not kind:
            return
        if kind not in outreach.KINDS:
            self.message_user(request, "Nothing was recorded: that email is not one of the drafts.", messages.ERROR)
            return
        if not outreach.can_email(obj):
            self.message_user(request, f"{obj} asked not to be emailed, so nothing was recorded.", messages.ERROR)
            return
        subject, _body = outreach.draft(obj.retailer, kind, outreach.figures(obj.retailer))
        outreach.mark_sent(obj, kind, subject)
        self.message_user(request, f"Marked as sent to {obj}. Follow up on {obj.follow_up_on:%-d %B %Y} if they "
                                   "have not replied.")

    def response_change(self, request, obj):
        if "_sent" in request.POST:
            from django.http import HttpResponseRedirect

            return HttpResponseRedirect(request.path)
        return super().response_change(request, obj)

    @admin.display(boolean=True, description="Email")
    def has_email(self, obj):
        return bool(obj.email)

    @admin.display(description="Shoppers (3 months)", ordering="visitors_quarter")
    def visitors_quarter(self, obj):
        return obj.visitors_quarter

    @admin.display(description="Listed", ordering="listed")
    def listed(self, obj):
        return obj.listed

    @admin.display(boolean=True, description="Affiliate link")
    def affiliate(self, obj):
        return bool(obj.retailer.affiliate_url_template.strip())

    def _figures(self, obj):
        from . import outreach

        found = getattr(obj, "_found", None)
        if found is None:
            found = obj._found = outreach.figures(obj.retailer)
        return found

    @admin.display(description="Figures")
    def figures(self, obj):
        from django.utils.html import format_html

        from . import outreach

        found = self._figures(obj)
        since = f", {found.visitors_total:,} since {found.since.day} {found.since:%B %Y}" if found.since else ""
        top = outreach.listed_names(found.top_products) or "none yet"
        return format_html(
            "<p>Shoppers sent: {} in the last 30 days, {} in the last 3 months{}.</p>"
            "<p>Listed now: {} products, {} in stock.</p><p>Most clicked in the last 3 months: {}.</p>"
            '<p style="color:var(--body-quiet-color)">People only: robots and direct hits are never counted.</p>',
            f"{found.visitors_month:,}", f"{found.visitors_quarter:,}", since, f"{found.listed:,}",
            f"{found.in_stock:,}", top,
        )

    @admin.display(description="Drafts")
    def drafts(self, obj):
        from django.utils.html import format_html, format_html_join

        from content import service as copy

        from . import outreach

        if not outreach.can_email(obj):
            return format_html("<p><strong>{} asked not to be emailed.</strong> No drafts are shown while the status "
                               "is Do not email.</p>", obj.retailer)
        found, store = self._figures(obj), copy.load_all()
        to = obj.email or ""
        rows = []
        for kind, name in outreach.DRAFTS:
            subject, body = outreach.draft(obj.retailer, kind, found, store)
            rows.append((name, subject, len(body.splitlines()) + 1, body, outreach.mailto(to, subject, body), kind))
        note = ("" if to else format_html(
            '<p style="color:var(--body-quiet-color)">No email address saved, so your mail app opens the draft '
            "with nobody to send it to. Add their address above and save to fill it in.</p>"))
        return format_html(
            '{}<div style="width:100%">{}</div><p style="color:var(--body-quiet-color)">Change the wording in Site '
            "wording, under Shop outreach emails.</p>",
            note,
            format_html_join(
                "",
                '<div style="margin:0 0 20px;max-width:46rem"><h3 style="margin:0 0 6px">{}</h3>'
                '<p style="margin:0 0 6px"><strong>Subject:</strong> {}</p>'
                '<textarea readonly rows="{}" style="width:100%;box-sizing:border-box">{}</textarea>'
                '<p style="margin:8px 0 0;display:flex;flex-wrap:wrap;gap:8px;align-items:center">'
                '<a class="button" style="padding:10px 15px" href="{}">Open in your mail app</a>'
                '<button type="submit" class="button" style="padding:10px 15px" name="_sent" value="{}">Mark as sent'
                "</button></p></div>",
                rows,
            ),
        )

    @admin.display(description="Sent")
    def sent(self, obj):
        from django.utils import timezone
        from django.utils.html import format_html, format_html_join

        from . import outreach

        names = dict(outreach.DRAFTS)
        emails = list(obj.emails.all()[:20])
        if not emails:
            return "None yet."
        return format_html("<ul style=\"margin:0;padding-left:1.2em\">{}</ul>", format_html_join(
            "", "<li>{}: {}. {}</li>",
            ((timezone.localtime(e.sent_at).strftime("%-d %b %Y"), names.get(e.kind, e.kind), e.subject)
             for e in emails),
        ))

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # The row keeps Do not email; deleting it would bring the shop back as Not contacted.
        return False
