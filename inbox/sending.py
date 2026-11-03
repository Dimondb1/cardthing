"""
Sending a shop outreach email from the app (inbox/outreach.py send_now), through the owner's own mailbox.

The owner, 3 Nov 2026: "I want to send emails each time from the app, I have a mailserver ... zepto for mass mail and
zoho is my normal emails". Each email goes over SMTP through the owner's Zoho Mail mailbox, set up on the Sending page
in Admin (inbox.OutreachMail), so it goes out as them and every reply lands in their inbox. It is never sent through
ZeptoMail: that service is for the automatic alert emails, its rules are for automatic emails only, and outreach sent
through it could cost the alerts their sender.

It sends one email when asked, never in a batch: a person presses Send now on each, as they pressed Open in your mail
app before. A day's limit keeps the mailbox trusted. The mailbox's password (an app password from Zoho) is kept in a file
only the site can read, saved from the Sending page, and is never shown again; the page keeps only its last four
characters. Nothing here touches ZeptoMail or the alerts.
"""

import logging
import os
import smtplib
import tempfile
from email.utils import formataddr
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.utils import timezone

from .models import OutreachEmail, OutreachMail

logger = logging.getLogger(__name__)

TIMEOUT = 20


class SendError(Exception):
    """An email that was not sent, worded for the owner."""


# The password ------------------------------------------------------------------------------------------------

def password_path():
    return Path(settings.RIPRAPTOR_OUTREACH_PASSWORD_FILE)


def password():
    try:
        return password_path().read_text().strip()
    except OSError:
        return ""


def write_password(text):
    """Write the password where only the site's user can read it, whole or not at all."""
    path = password_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".outreach-mail-")
    try:
        os.chmod(temp, 0o600)
        os.write(handle, text.encode())
        os.fsync(handle)
        os.close(handle)
        os.replace(temp, path)
    except BaseException:
        try:
            os.close(handle)
        except OSError:
            pass
        if os.path.exists(temp):
            os.unlink(temp)
        raise


def save_password(text, now=None):
    """Keep a new password. The row remembers only its last four characters."""
    text = (text or "").strip()
    write_password(text)
    OutreachMail.objects.filter(pk=OutreachMail.load().pk).update(
        password_hint=text[-4:], password_saved_at=now or timezone.now(), problem="")


# Whether it can send -----------------------------------------------------------------------------------------

def why_not(row=None):
    """Why an email cannot be sent from the app now, worded for the owner, or ""."""
    row = row or OutreachMail.current()
    if not row.on:
        return "Sending from RipRaptor is switched off."
    if not (row.host and row.username and row.sender):
        return "Sending from RipRaptor is not set up: its mail server and your mailbox are needed."
    if not password():
        return "Sending from RipRaptor is not set up: your mailbox's app password is needed."
    return ""


def ready(row=None):
    return not why_not(row)


def sent_today(now=None):
    """How many shop emails were sent or marked as sent today (UK time)."""
    now = now or timezone.now()
    start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return OutreachEmail.objects.filter(sent_at__gte=start).count()


def room_today(row=None, now=None):
    row = row or OutreachMail.current()
    return max(row.daily_limit - sent_today(now), 0)


# Sending -------------------------------------------------------------------------------------------------------

def connection(row):
    return get_connection(host=row.host, port=row.port, username=row.username, password=password(),
                          use_ssl=row.port == 465, use_tls=row.port != 465, timeout=TIMEOUT, fail_silently=False)


def explain(exc):
    """What went wrong, for the owner, without the server's own jargon."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ("Zoho did not accept the mailbox or its app password. Make a new app password in Zoho and save it on "
                "the Sending page.")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The shop's address was refused, so the email was not sent. Check the address."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return ("Zoho would not send from that address. It must be your mailbox or an alias you have added to it in "
                "Zoho.")
    if isinstance(exc, smtplib.SMTPDataError):
        return f"Zoho refused the email: {str(exc)[:150]}"
    if isinstance(exc, (smtplib.SMTPServerDisconnected, TimeoutError, OSError)):
        return f"Could not reach the mail server ({str(exc)[:100]}). Nothing was sent."
    return f"The email could not be sent ({str(exc)[:150]})."


def message(row, to, subject, body):
    """The email as plain text from the owner: their name and address, replies back to them, a copy to them."""
    sender = formataddr((row.from_name, row.sender))
    return EmailMessage(subject, body, from_email=sender, to=[to], reply_to=[row.sender],
                        bcc=[row.sender] if row.copy_to_me else None, connection=connection(row))


def send(to, subject, body, row=None):
    """Send one plain text email from the owner's mailbox. Raises SendError (worded for the owner) and notes why on
    the row; nothing is sent when it is not set up."""
    row = row or OutreachMail.current()
    why = why_not(row)
    if why:
        raise SendError(why)
    try:
        message(row, to, subject, body).send()
    except Exception as exc:   # noqa: BLE001 - any failure is the owner's to read, never a crash
        reason = explain(exc)
        logger.warning("Outreach email to %s not sent: %s", to, exc)
        OutreachMail.objects.filter(pk=OutreachMail.load().pk).update(problem=reason[:300])
        raise SendError(reason) from exc
    if row.problem:
        OutreachMail.objects.filter(pk=OutreachMail.load().pk).update(problem="")


def send_test(subject, body):
    """An email to the owner's own mailbox, to see how it arrives. Not recorded against any shop."""
    row = OutreachMail.current()
    send(row.sender, f"[Test] {subject}", body, row)
    return row.sender
