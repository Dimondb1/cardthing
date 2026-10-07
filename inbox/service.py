"""
Message us: visitors write, the owner reads and replies in Django Admin.

New messages reach the owner by an email to their own address through
ZeptoMail (RIPRAPTOR_INBOX_NOTIFY_EMAIL) and, if set, a phone push through
ntfy (RIPRAPTOR_NTFY_TOPIC). The push carries no name and no message text,
only a link to admin, because ntfy.sh topics are public to anyone who guesses
the name. Either can be left blank.
"""

import urllib.request
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from catalogue import mail

from .models import Conversation, Message

KEEP_DAYS = 183
MAX_LENGTH = 3000
PER_THREAD_PER_HOUR = 6
NEW_THREADS_PER_HOUR = 40


class Refused(Exception):
    """Too many messages for now."""


def purge(now=None):
    now = now or timezone.now()
    Conversation.objects.filter(last_message_at__lt=now - timedelta(days=KEEP_DAYS)).delete()


def start(body, name="", email="", now=None):
    now = now or timezone.now()
    purge(now)
    if Conversation.objects.filter(created_at__gte=now - timedelta(hours=1)).count() >= NEW_THREADS_PER_HOUR:
        raise Refused
    conversation = Conversation.objects.create(name=name.strip()[:80], email=email.strip().lower(), created_at=now, last_message_at=now)
    message = Message.objects.create(conversation=conversation, body=body.strip()[:MAX_LENGTH], created_at=now)
    tell_owner(conversation, message)
    return conversation


def add(conversation, body, now=None):
    now = now or timezone.now()
    recent = conversation.messages.filter(from_visitor=True, created_at__gte=now - timedelta(hours=1)).count()
    if conversation.closed or recent >= PER_THREAD_PER_HOUR:
        raise Refused
    message = Message.objects.create(conversation=conversation, body=body.strip()[:MAX_LENGTH], created_at=now)
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=now, unread=True)
    tell_owner(conversation, message)
    return message


def reply(conversation, body, now=None):
    """Store the owner's reply and email the visitor if they left an address. Returns True when emailed."""
    now = now or timezone.now()
    Message.objects.create(conversation=conversation, from_visitor=False, body=body.strip()[:MAX_LENGTH], created_at=now)
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=now, unread=False)
    if not (conversation.email and mail.enabled()):
        return False
    from catalogue.alerts import render_mail

    thread_url = settings.RIPRAPTOR_SITE_URL + conversation.get_absolute_url()
    subject = f"{settings.RIPRAPTOR_SITE_NAME} replied to your message"
    text, html = render_mail("contact_reply", {
        "subject": subject, "preheader": body.strip()[:90], "reply": body.strip(),
        "thread_url": thread_url, "stop_url": thread_url + "?forget=1",
    })
    mail.send(conversation.email, subject, text, html, unsubscribe=thread_url + "?forget=1")
    return True


def admin_url(conversation):
    return settings.RIPRAPTOR_SITE_URL + reverse("admin:inbox_conversation_change", args=[conversation.pk])


def tell_owner(conversation, message, opener=None):
    """Best effort: a failed notification never loses the message, which is already saved."""
    who = conversation.name or "A visitor"
    link = admin_url(conversation)
    if settings.RIPRAPTOR_NTFY_TOPIC:
        request = urllib.request.Request(
            settings.RIPRAPTOR_NTFY_URL.rstrip("/") + "/" + settings.RIPRAPTOR_NTFY_TOPIC,
            data=b"Open admin to read it.",
            headers={"Title": f"New {settings.RIPRAPTOR_SITE_NAME} message", "Click": link, "Tags": "speech_balloon"},
            method="POST",
        )
        try:
            with (opener or urllib.request.urlopen)(request, timeout=10):
                pass
        except OSError:
            pass
    if settings.RIPRAPTOR_INBOX_NOTIFY_EMAIL and mail.enabled():
        text = f"{who} wrote:\n\n{message.body}\n\nRead and reply: {link}\n"
        html = f"<p><strong>{escape(who)}</strong> wrote:</p><p style=\"white-space:pre-wrap\">{escape(message.body)}</p><p><a href=\"{link}\">Read and reply</a></p>"
        try:
            mail.send(settings.RIPRAPTOR_INBOX_NOTIFY_EMAIL, f"New message from {who}", text, html)
        except mail.MailError:
            pass


def escape(value):
    from django.utils.html import escape as html_escape

    return html_escape(value)


def forget_email(conversation):
    Conversation.objects.filter(pk=conversation.pk).update(email="")


def unread_count():
    return Conversation.objects.filter(unread=True).count()
