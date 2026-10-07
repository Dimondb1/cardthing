from django import forms
from django.contrib import admin, messages

from catalogue import mail

from . import service
from .models import Conversation


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
            "", '<div style="margin:0 0 10px;padding:10px 14px;border-radius:10px;max-width:46rem;{}"><div style="font-size:12px;opacity:.75;margin-bottom:4px"><strong>{}</strong> {}</div>{}</div>',
            ((("background:#f2f4f7;" if m.from_visitor else "background:#fff1ea;border:1px solid #ffd6c4;"),
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
