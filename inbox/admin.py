from django import forms
from django.contrib import admin, messages
from django.utils import timezone

from catalogue import mail

from . import service
from .models import Conversation, OutreachMail, ShopContact, ShopEnquiry


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
    """Shops asking to be listed (inbox/shops.py). The shop checker looks at each website as it arrives; Approve adds
    the shop, saves its discount code and sends the welcome email once. Nothing else is ever sent from here. The
    website is shown as text: it is what they typed."""

    list_display = ("name", "domain", "email", "created_at", "check_state", "approved", "handled")
    list_filter = (HandledFilter,)
    search_fields = ("name", "domain", "shop_url", "email", "message", "note", "discount_code")
    date_hierarchy = "created_at"
    list_select_related = ("shop_check",)
    readonly_fields = ("name", "shop_url", "domain", "email", "email_check", "discount_code", "discount_terms",
                       "message", "created_at", "check_result", "shop_link", "approved_at", "welcome_sent_at",
                       "welcome_preview")
    fields = ("check_result", "name", "shop_url", "domain", "email", "email_check", "discount_code", "discount_terms",
              "message", "created_at", "shop_link", "approved_at", "welcome_sent_at", "welcome_preview",
              "handled_at", "note")
    actions = ("mark_handled",)
    change_form_template = "admin/inbox/shopenquiry/change_form.html"

    @admin.display(boolean=True, description="Handled", ordering="handled_at")
    def handled(self, obj):
        return obj.handled_at is not None

    @admin.display(boolean=True, description="Approved", ordering="approved_at")
    def approved(self, obj):
        return obj.approved_at is not None

    @admin.display(description="Check")
    def check_state(self, obj):
        return obj.shop_check.get_status_display() if obj.shop_check_id else "Not checked"

    @admin.display(description="What the check found")
    def check_result(self, obj):
        from django.utils.html import format_html

        row = obj.shop_check
        if row is None:
            return "Not checked: too many shops asked today. Press Check the website."
        when = f" Checked {timezone.localtime(row.checked_at):%d %b %H:%M}." if row.checked_at else ""
        return format_html("<strong>{}</strong> {}{}", row.get_status_display(), row.note, when)

    @admin.display(description="Is the email the shop's")
    def email_check(self, obj):
        from . import shops

        if shops.email_matches(obj):
            return f"On the shop's own domain ({obj.domain})."
        return (f"Not on the shop's own domain ({obj.domain}). Make sure it is really the shop's before approving: "
                "the welcome email goes to it.")

    @admin.display(description="Shop on RipRaptor")
    def shop_link(self, obj):
        from django.urls import reverse
        from django.utils.html import format_html

        if obj.retailer_id is None:
            return "Added when you approve it."
        return format_html('<a href="{}">{}</a>', reverse("admin:catalogue_retailer_change", args=[obj.retailer_id]),
                           obj.retailer.name)

    @admin.display(description="Welcome email")
    def welcome_preview(self, obj):
        from django.utils.html import format_html

        from . import shops

        subject, body = shops.welcome(obj)
        return format_html('<div style="max-width: 40rem; min-width: 0;"><p><strong>{}</strong></p>'
                           '<div style="white-space: pre-wrap; overflow-wrap: anywhere;">{}</div></div>', subject, body)

    def response_change(self, request, obj):
        from django.contrib import messages
        from django.http import HttpResponseRedirect

        from . import shops

        if "_approve" in request.POST:
            try:
                shop, why = shops.approve(obj)
            except shops.NotReady as exc:
                messages.error(request, f"Not approved: {exc}")
            else:
                listed = f"{shop.name} is listed and switched on"
                if why:
                    messages.warning(request, f"{listed}, but the welcome email was not sent: {why}")
                else:
                    messages.success(request, f"{listed}. The welcome email went to {obj.email}.")
            return HttpResponseRedirect(request.path)
        if "_send_welcome" in request.POST:
            why = shops.send_welcome(obj)
            if why:
                messages.error(request, f"Not sent: {why}")
            else:
                messages.success(request, f"The welcome email went to {obj.email}.")
            return HttpResponseRedirect(request.path)
        if "_recheck" in request.POST:
            shops.check_again(obj)
            messages.success(request, "The website will be checked within a few minutes. Reload this page to see it.")
            return HttpResponseRedirect(request.path)
        return super().response_change(request, obj)

    @admin.action(description="Mark handled")
    def mark_handled(self, request, queryset):
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


class OutreachMailForm(forms.ModelForm):
    password = forms.CharField(
        label="App password", required=False, widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Only for sending through your Zoho mailbox. Paste a new one to change it. It is kept in a file only "
                  "the site can read and never shown again. Leave empty to keep the one saved.")
    token = forms.CharField(
        label="Shop emails' own ZeptoMail token", required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        help_text="Optional. In ZeptoMail, add a Mail Agent called Shop emails, open its Send Mail token and paste it "
                  "here, so shop emails are kept apart from the alert emails. Leave empty to keep the one saved, or "
                  "to use the alert emails' token. Kept in a file only the site can read and never shown again.")
    forget_token = forms.BooleanField(
        label="Use the alert emails' token instead", required=False,
        help_text="Forgets the token saved above.")
    send_test = forms.BooleanField(
        label="Send me a test email when I save", required=False,
        help_text="Sends the first shop's email to the Replies go to address, so you see how it arrives.")

    class Meta:
        model = OutreachMail
        fields = ("on", "service", "host", "port", "username", "from_name", "from_address", "reply_to", "copy_to_me",
                  "daily_limit")

    def clean(self):
        from . import sending

        data = super().clean()
        if data.get("port") not in (465, 587):
            self.add_error("port", "Use 465 (Zoho's usual) or 587.")
        if not data.get("on"):
            return data
        if data.get("service") == OutreachMail.Service.ZEPTOMAIL:
            sender = data.get("from_address") or ""
            if not sender:
                self.add_error("from_address", "Add the address shops see, such as info@ripraptor.com.")
            elif not sending.on_mail_domain(sender):
                self.add_error("from_address", f"Through ZeptoMail it must be an address on {sending.mail_domain()}, "
                                               "the domain ZeptoMail sends the alert emails for.")
            has_token = data.get("token") or (sending.own_token() and not data.get("forget_token")) or \
                sending.alerts_token()
            if not has_token:
                self.add_error("token", "ZeptoMail has no token on the server. Paste one to switch sending on.")
        else:
            if not data.get("username"):
                self.add_error("username", "Add your mailbox address to switch sending on.")
            if not (data.get("password") or sending.password()):
                self.add_error("password", "Add the app password to switch sending on.")
        return data


@admin.register(OutreachMail)
class OutreachMailAdmin(admin.ModelAdmin):
    """Sending shop emails from the app (inbox/sending.py): through ZeptoMail, as the alert emails, or the owner's own
    Zoho mailbox. The list goes straight to the one row."""

    form = OutreachMailForm
    save_on_top = True
    readonly_fields = ("status",)
    fieldsets = (
        (None, {"fields": ("status",)}),
        ("Set up", {
            "description": "Tick Send from RipRaptor, give the address shops see and where replies go, tick Send me "
                           "a test email and save.",
            "fields": ("on", "service", "from_name", "from_address", "reply_to"),
        }),
        ("ZeptoMail", {
            "description": "It uses the ZeptoMail the alert emails use, so it works without anything more. ZeptoMail's "
                           "terms are for transactional email, and Zoho can close the account for too many bounces or "
                           "complaints, which would stop the alert emails too. A Mail Agent just for shop emails keeps "
                           "their figures apart.",
            "fields": ("token", "forget_token"),
        }),
        ("Limits and copies", {"fields": ("daily_limit", "copy_to_me", "send_test")}),
        ("Your Zoho mailbox", {
            "description": "Only when Send through is your Zoho mailbox. In Zoho, open Accounts (accounts.zoho.com), "
                           "then Security, then App passwords, and make one called RipRaptor. Your normal Zoho "
                           "password is never needed. The server must be able to reach Zoho's mail server on port 465 "
                           "or 587; many hosts block it.",
            "fields": ("username", "password", "host", "port"),
        }),
    )

    def has_add_permission(self, request):
        return False   # the list opens the one row, made on first use

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from django.http import HttpResponseRedirect
        from django.urls import reverse

        return HttpResponseRedirect(reverse("admin:inbox_outreachmail_change", args=[OutreachMail.load().pk]))

    @admin.display(description="Status")
    def status(self, obj):
        from django.utils.html import format_html

        from . import sending

        why = sending.why_not(obj)
        if obj.by_zeptomail:
            from catalogue import zoho_mail

            if zoho_mail.in_use():
                secret = (f"Through Zoho Mail's web API from {zoho_mail.saved().get('from_address')}, as every site "
                          "email is now (Admin, Email sending).")
            elif obj.token_hint and sending.own_token():
                secret = f"Through ZeptoMail, with the shop emails' own token (ends {obj.token_hint})."
            else:
                secret = "Through ZeptoMail, with the alert emails' token."
        else:
            secret = (f"Through your Zoho mailbox. App password saved (ends {obj.password_hint})." if obj.password_hint
                      else "Through your Zoho mailbox. No app password saved yet.")
        return format_html(
            "<p><strong>{}</strong></p><p>{} {} of {} sent today.</p>{}",
            why or "Ready: Send now is on every shop email.", secret, sending.sent_today(), obj.daily_limit,
            format_html("<p>Last problem: {}</p>", obj.problem) if obj.problem else "")

    def save_model(self, request, obj, form, change):
        from . import outreach, sending

        super().save_model(request, obj, form, change)
        try:
            new = form.cleaned_data.get("password", "").strip()
            if new:
                sending.save_password(new)
                self.message_user(request, "Password saved. It is not shown again.")
            new = form.cleaned_data.get("token", "").strip()
            if new:
                sending.save_token(new)
                self.message_user(request, "Token saved. It is not shown again.")
            elif form.cleaned_data.get("forget_token") and sending.own_token():
                sending.forget_token()
                self.message_user(request, "Token forgotten: shop emails use the alert emails' token.")
        except OSError:
            self.message_user(request, "The server could not save it, so nothing was changed there.", messages.ERROR)
            return
        if form.cleaned_data.get("send_test"):
            contact = next(iter(outreach.update_queue()[:1]), None)
            if contact:
                subject, body = outreach.draft(contact.retailer, outreach.UPDATE, outreach.figures(contact.retailer))
            else:
                subject, body = "RipRaptor", "This is a test from RipRaptor. If you can read this, sending works."
            try:
                address = sending.send_test(subject, body)
            except sending.SendError as exc:
                self.message_user(request, f"The test was not sent: {exc}", messages.ERROR)
            else:
                self.message_user(request, f"Test sent to {address}. Check your inbox: it arrives as a shop's would.")

    def response_change(self, request, obj):
        from django.http import HttpResponseRedirect

        return HttpResponseRedirect(request.path)


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
    change_list_template = "admin/shopcontact_change_list.html"

    class Media:
        js = ("js/copy-mail.js",)
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

    # The update and badge email to every shop in one place ------------------------------------------------------

    def get_urls(self):
        from django.urls import path

        return [path("update-emails/", self.admin_site.admin_view(self.update_emails_view),
                     name="inbox_shopcontact_update_emails")] + super().get_urls()

    def update_emails_view(self, request):
        """One ready-to-send update and badge email for each shop worth writing to, the ones sending most shoppers
        first. Each has its own address box, the draft, Send now (once sending is set up on the Sending page), Open in
        your mail app and Mark as sent, so the owner works down the page. Nothing is sent but by pressing Send now."""
        from urllib.parse import urlsplit

        from django.core.exceptions import PermissionDenied
        from django.template.response import TemplateResponse

        from content import service as copy
        from django.utils import timezone

        from . import outreach, sending, shop_emails
        from .models import OutreachEmail

        if not self.has_change_permission(request):
            raise PermissionDenied
        outreach.ensure_contacts()
        if request.method == "POST":
            return self._update_emails_post(request)
        show_sent = request.GET.get("show") == "all"
        contacts = list(outreach.update_queue(show_sent))
        found = outreach.bulk_figures([contact.retailer_id for contact in contacts])
        store = copy.load_all()
        sent_on = {}
        for contact_id, when in OutreachEmail.objects.filter(kind=outreach.UPDATE).order_by("sent_at").values_list(
                "contact_id", "sent_at"):
            sent_on[contact_id] = timezone.localtime(when)
        rows = []
        for contact in contacts:
            subject, body = outreach.draft(contact.retailer, outreach.UPDATE, found[contact.retailer_id], store)
            # Rows for the box to show the whole email: a long line wraps, so count it as the lines it takes.
            lines = sum(max(1, -(-len(line) // 60)) for line in body.splitlines()) + 1
            found_on = urlsplit(contact.email_found_on)
            rows.append({"contact": contact, "subject": subject, "body": body, "lines": lines,
                         "mailto": outreach.mailto(contact.email or "", subject, body),
                         "mailto_short": outreach.mailto(contact.email or "", subject),
                         "sent_on": sent_on.get(contact.pk),
                         "found_label": f"{found_on.hostname or ''}{found_on.path}"[:60] if contact.email_found_on else "",
                         "looked_at": timezone.localtime(contact.email_looked_at) if contact.email_looked_at else None})
        context = dict(self.admin_site.each_context(request), title="Update and badge emails", opts=self.model._meta,
                       rows=rows, show_sent=show_sent, no_address=sum(1 for row in rows if not row["contact"].email),
                       looking=shop_emails.running(), looked=sum(1 for row in rows if row["contact"].email_looked_at),
                       sending=sending.ready(), not_sending=sending.why_not(), room_today=sending.room_today())
        return TemplateResponse(request, "admin/shop_update_emails.html", context)

    def _update_emails_post(self, request):
        """Save the address typed under one shop and, for Mark as sent, record the email. Back to the page."""
        from django.core.exceptions import ValidationError
        from django.core.validators import validate_email
        from django.http import Http404, HttpResponseRedirect

        from . import outreach, shop_emails

        if "_find" in request.POST:
            if shop_emails.start_now():
                self.message_user(request, "Looking for the missing addresses now. It takes a few minutes: refresh "
                                           "this page to see them appear, each with the page it was found on.")
            elif shop_emails.running():
                self.message_user(request, "Already looking for addresses. Refresh this page in a minute.")
            else:
                self.message_user(request, "Could not start looking for addresses.", messages.ERROR)
            return HttpResponseRedirect(request.get_full_path())
        try:
            contact = ShopContact.objects.select_related("retailer").get(pk=int(request.POST.get("contact", "")))
        except (ValueError, ShopContact.DoesNotExist):
            raise Http404
        back = request.get_full_path()
        address = request.POST.get("email", "").strip()
        try:
            if address:
                validate_email(address)
        except ValidationError:
            self.message_user(request, f"{contact}: that email address does not look right, so nothing was saved.",
                              messages.ERROR)
            return HttpResponseRedirect(f"{back}#shop-{contact.pk}")
        if address != contact.email:
            # An address typed here is the owner's own: where one was found no longer applies.
            contact.email, contact.email_found_on = address, ""
            contact.save(update_fields=["email", "email_found_on"])
        if "_sent" not in request.POST and "_send" not in request.POST:
            self.message_user(request, f"Saved the address for {contact}.")
            return HttpResponseRedirect(f"{back}#shop-{contact.pk}")
        if "_send" in request.POST:
            self._send(request, contact, outreach.UPDATE)
            return HttpResponseRedirect(back)
        if not outreach.can_email(contact):
            self.message_user(request, f"{contact} asked not to be emailed, so nothing was recorded.", messages.ERROR)
            return HttpResponseRedirect(back)
        subject, _body = outreach.draft(contact.retailer, outreach.UPDATE, outreach.figures(contact.retailer))
        outreach.mark_sent(contact, outreach.UPDATE, subject)
        self.message_user(request, f"Marked as sent to {contact}. Follow up on {contact.follow_up_on:%-d %B %Y} if "
                                   "they have not replied.")
        return HttpResponseRedirect(back)

    def save_model(self, request, obj, form, change):
        """Saves the row, then, for Mark as sent under a draft, records the email and sets the follow-up. The button
        is part of this form, so an address typed in above is kept too."""
        from . import outreach

        if "email" in form.changed_data:
            obj.email_found_on = ""
        super().save_model(request, obj, form, change)
        if request.POST.get("_send"):
            self._send(request, obj, request.POST["_send"])
            return
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

    def _send(self, request, contact, kind):
        """Send the draft from the app (inbox/outreach.py send_now) and say what happened. Nothing is recorded unless
        it went."""
        from . import outreach, sending

        try:
            _subject, address = outreach.send_now(contact, kind)
        except sending.SendError as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return False
        self.message_user(request, f"Sent to {contact} at {address}. Follow up on {contact.follow_up_on:%-d %B %Y} if "
                                   "they have not replied.")
        return True

    def response_change(self, request, obj):
        if "_sent" in request.POST or "_send" in request.POST:
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

        from django.urls import reverse

        from . import outreach, sending

        if not outreach.can_email(obj):
            return format_html("<p><strong>{} asked not to be emailed.</strong> No drafts are shown while the status "
                               "is Do not email.</p>", obj.retailer)
        found, store = self._figures(obj), copy.load_all()
        to = obj.email or ""
        can_send = sending.ready()
        rows = []
        for kind, name in outreach.DRAFTS:
            subject, body = outreach.draft(obj.retailer, kind, found, store)
            send = (format_html('<button type="submit" class="button default" style="padding:10px 15px" name="_send" '
                                'value="{}" data-confirm="Send this email to {} now?">Send now</button>', kind, obj.retailer.name)
                    if can_send else "")
            rows.append((name, subject, len(body.splitlines()) + 1, body, outreach.mailto(to, subject, body), kind,
                         outreach.mailto(to, subject), send))
        if not can_send:
            note = format_html(
                '<p style="color:var(--body-quiet-color)">{} <a href="{}">Set it up</a> to send from here.</p>',
                sending.why_not(), reverse("admin:inbox_outreachmail_changelist"))
        else:
            note = ""
        if not to:
            note = format_html(
                '{}<p style="color:var(--body-quiet-color)">No email address saved, so there is nobody to send it to. '
                "Add their address above and save.</p>", note)
        return format_html(
            '{}<p style="color:var(--body-quiet-color)">Open in your mail app copies the message first. In the app, '
            "press and hold in the empty message and choose Paste: some mail apps run a filled-in message's lines "
            'together.</p><div style="width:100%">{}</div><p style="color:var(--body-quiet-color)">Change the wording '
            "in Site wording, under Shop outreach emails.</p>",
            note,
            format_html_join(
                "",
                '<div style="margin:0 0 20px;max-width:46rem"><h3 style="margin:0 0 6px">{0}</h3>'
                '<p style="margin:0 0 6px"><strong>Subject:</strong> {1}</p>'
                '<textarea readonly id="body-{5}" rows="{2}" style="width:100%;box-sizing:border-box;height:auto;'
                'max-height:none;field-sizing:content">{3}</textarea>'
                '<p style="margin:8px 0 0;display:flex;flex-wrap:wrap;gap:8px;align-items:center">'
                '{7}<a class="button" style="padding:10px 15px" href="{4}" data-copy-from="body-{5}" data-short="{6}">'
                'Open in your mail app</a>'
                '<button type="submit" class="button" style="padding:10px 15px" name="_sent" value="{5}">Mark as sent'
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
