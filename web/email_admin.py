"""
Email sending: which way every email the site sends goes out (ZeptoMail, or the owner's Zoho mailbox through Zoho Mail's
web API, catalogue/zoho_mail.py), connecting Zoho Mail, and a test email that says plainly what happened. Staff only,
never for editors. The owner, 14 Nov 2026: "ZeptoMail has to verify my account, so it is no longer sending emails ...
can you look into whether we can just move the outgoing emails onto Zoho".

The Client Secret and the code are typed here, never into a message, and kept only in a file the site can read; the
page never shows them again.
"""

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from catalogue import mail, zoho_mail
from guides.access import not_for_editors
from inbox import sending
from inbox.models import OutreachMail

TEST_SUBJECT = "[Test] RipRaptor email sending"


def route_name():
    return "Zoho Mail" if zoho_mail.in_use() else "ZeptoMail"


def test_address():
    """Where a test goes unless another address is typed: the owner's own inbox."""
    return settings.RIPRAPTOR_INBOX_NOTIFY_EMAIL or OutreachMail.current().inbox or ""


def send_test(request):
    to = request.POST.get("to", "").strip()
    try:
        validate_email(to)
    except ValidationError:
        messages.error(request, "Type the address to send the test to.")
        return
    route = route_name()
    if not mail.enabled():
        messages.error(request, "Email is not set up: there is no ZeptoMail token on the server and Zoho Mail is not "
                                "switched on. Connect Zoho Mail below.")
        return
    try:
        mail.send(to, TEST_SUBJECT, f"This is a test from Admin, Email sending. It went through {route}.", "")
    except mail.MailError as exc:
        messages.error(request, f"Not sent through {route}: {sending.explain(exc)}")
        return
    messages.success(request, f"Test sent to {to} through {route}. If it is not in the inbox within a few minutes, "
                              "look in the spam folder.")


def connect(request):
    try:
        data = zoho_mail.connect(request.POST.get("region", ""), request.POST.get("client_id", ""),
                                 request.POST.get("client_secret", ""), request.POST.get("code", ""))
        if request.POST.get("use_now"):
            zoho_mail.use(True)
    except mail.MailError as exc:
        messages.error(request, f"Zoho Mail is not connected: {str(exc).rstrip('.')}. Nothing was changed.")
        return
    now = ("Every email the site sends now goes through it" if request.POST.get("use_now")
           else "Site emails still go through ZeptoMail until you choose Zoho Mail below")
    messages.success(request, f"Connected to Zoho Mail: {data['mailbox']}. {now}, from {data['from_address']}. Send a "
                              "test to check.")


def choose(request):
    on = request.POST.get("route") == "zoho"
    try:
        zoho_mail.use(on, request.POST.get("from_address") or None)
    except mail.MailError as exc:
        messages.error(request, f"{str(exc).rstrip('.')}. Nothing was changed.")
        return
    if on:
        messages.success(request, f"Every email the site sends now goes through Zoho Mail, from "
                                  f"{zoho_mail.saved()['from_address']}. Send a test to check.")
    else:
        messages.success(request, "Every email the site sends goes through ZeptoMail again.")


@staff_member_required
@not_for_editors
@sensitive_post_parameters("client_secret", "code")
@require_http_methods(["GET", "POST"])
def email_page(request):
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "test":
            send_test(request)
        elif action == "connect":
            connect(request)
        elif action == "choose":
            choose(request)
        elif action == "forget":
            zoho_mail.forget()
            messages.success(request, "Zoho Mail is disconnected. Every email the site sends goes through ZeptoMail.")
        return redirect(reverse("email_sending"))
    data = zoho_mail.saved()
    response = render(request, "admin/email_sending.html", {
        **admin.site.each_context(request),
        "title": "Email sending",
        "route": route_name(),
        "zeptomail_ready": mail.zeptomail_ready(),
        "enabled": mail.enabled(),
        "connected": zoho_mail.connected(data),
        "zoho": {key: data.get(key) for key in ("mailbox", "addresses", "from_address", "region", "connected_at")},
        "region_label": zoho_mail.REGIONS.get(data.get("region"), ("",))[0],
        "regions": [(key, label) for key, (label, _accounts, _mail) in zoho_mail.REGIONS.items()],
        "scopes": zoho_mail.SCOPES,
        "test_to": test_address(),
        "can_save": bool(settings.RIPRAPTOR_ZOHO_MAIL_FILE),
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
