"""
Sending a shop outreach email from the app (inbox/outreach.py send_now): one email when the owner presses Send now.

The owner, 3 Nov 2026, first: "I want to send emails each time from the app, I have a mailserver ... zepto for mass
mail and zoho is my normal emails". That sent through their Zoho mailbox over SMTP, and the test timed out: the server
could not reach Zoho's mail server, as many hosts block mail ports. Then: "I want to use zepto to send out". So by
default each email goes through ZeptoMail (catalogue/mail.py), the way the alert emails already leave the server, over
the web: from an address on the site's domain, as plain text with no tracking, replies and a copy to the owner's
inbox. ZeptoMail's terms are for transactional email, and the account can be closed for bounces or complaints, which
would stop the alert emails too; the owner chose it knowing that, and a token of a Mail Agent just for shop emails
can be saved on the Sending page to keep them apart. The Zoho mailbox stays as the other choice (OutreachMail.service).

It sends one email when asked, never in a batch, and a day's limit keeps the sender trusted. The mailbox's app
password and the shop emails' own ZeptoMail token are kept in files only the site can read, saved from the Sending
page and never shown again; the page keeps only their last four characters.
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

from catalogue import mail as zeptomail

from .models import OutreachEmail, OutreachMail

logger = logging.getLogger(__name__)

TIMEOUT = 20


class SendError(Exception):
    """An email that was not sent, worded for the owner."""


# The secrets: the mailbox's app password and the shop emails' own ZeptoMail token ----------------------------

def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _write(path, text):
    """Write a secret where only the site's user can read it, whole or not at all."""
    path = Path(path)
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


def _note(**fields):
    OutreachMail.objects.filter(pk=OutreachMail.load().pk).update(**fields)


def password_path():
    return Path(settings.RIPRAPTOR_OUTREACH_PASSWORD_FILE)


def password():
    return _read(password_path())


def write_password(text):
    _write(password_path(), text)


def save_password(text, now=None):
    """Keep a new mailbox password. The row remembers only its last four characters."""
    text = (text or "").strip()
    write_password(text)
    _note(password_hint=text[-4:], password_saved_at=now or timezone.now(), problem="")


def token_path():
    return Path(settings.RIPRAPTOR_OUTREACH_TOKEN_FILE)


def own_token():
    """The token of a ZeptoMail Mail Agent for shop emails alone, if one is saved."""
    return _read(token_path())


def alerts_token():
    return settings.RIPRAPTOR_ZEPTOMAIL_TOKEN


def token():
    """The ZeptoMail token shop emails go with: their own if saved, else the alert emails'."""
    return own_token() or alerts_token()


def mail_domain():
    """The domain ZeptoMail sends the alert emails for, so one it will send shop emails for too."""
    return settings.RIPRAPTOR_MAIL_FROM.rpartition("@")[2].lower()


def on_mail_domain(address):
    domain = address.rpartition("@")[2].lower()
    return bool(mail_domain()) and (domain == mail_domain() or domain.endswith("." + mail_domain()))


def save_token(text):
    text = (text or "").strip()
    _write(token_path(), text)
    _note(token_hint=text[-4:], problem="")


def forget_token():
    """Back to the alert emails' token."""
    try:
        token_path().unlink()
    except FileNotFoundError:
        pass
    _note(token_hint="")


# Whether it can send -----------------------------------------------------------------------------------------

def why_not(row=None):
    """Why an email cannot be sent from the app now, worded for the owner, or ""."""
    row = row or OutreachMail.current()
    if not row.on:
        return "Sending from RipRaptor is switched off."
    if row.by_zeptomail:
        if not row.from_address:
            return "Sending from RipRaptor is not set up: the Sent from address is needed."
        if not on_mail_domain(row.from_address):
            return f"Sending from RipRaptor is not set up: the Sent from address must be on {mail_domain()}."
        if not token():
            return ("Sending from RipRaptor is not set up: ZeptoMail has no token on the server. Paste one on the "
                    "Sending page.")
        return ""
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


# Sending through the Zoho mailbox (SMTP) ---------------------------------------------------------------------

def connection(row):
    return get_connection(host=row.host, port=row.port, username=row.username, password=password(),
                          use_ssl=row.port == 465, use_tls=row.port != 465, timeout=TIMEOUT, fail_silently=False)


def message(row, to, subject, body):
    """The email as plain text from the owner: their name and address, replies back to them, a copy to them."""
    sender = formataddr((row.from_name, row.sender))
    return EmailMessage(subject, body, from_email=sender, to=[to], reply_to=[row.inbox],
                        bcc=[row.inbox] if row.copy_to_me else None, connection=connection(row))


# Sending ------------------------------------------------------------------------------------------------------

def explain(exc):
    """What went wrong, for the owner, without the server's own jargon."""
    if isinstance(exc, zeptomail.MailError):
        text = str(exc)
        if " 401" in text or " 403" in text:
            return ("ZeptoMail did not accept the token. Paste the Send Mail token from your Mail Agent in ZeptoMail "
                    "on the Sending page.")
        if "could not be reached" in text:
            return f"ZeptoMail could not be reached ({text[:120]}). Nothing was sent."
        return f"ZeptoMail refused the email: {text[:200]}. Check the Sent from address is on {mail_domain()}."
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
        return (f"Could not reach the mail server ({str(exc)[:100]}). Nothing was sent. The server's host may block "
                "mail ports: choose ZeptoMail under Send through.")
    return f"The email could not be sent ({str(exc)[:150]})."


def _deliver(row, to, subject, body):
    if row.by_zeptomail:
        zeptomail.send(to, subject, body, "", sender=(row.sender, row.from_name), reply_to=row.inbox,
                       bcc=row.inbox if row.copy_to_me else "", token=token(), tracking=False)
    else:
        message(row, to, subject, body).send()


def send(to, subject, body, row=None):
    """Send one plain text email the way the Sending page says. Raises SendError (worded for the owner) and notes why
    on the row; nothing is sent when it is not set up."""
    row = row or OutreachMail.current()
    why = why_not(row)
    if why:
        raise SendError(why)
    try:
        _deliver(row, to, subject, body)
    except Exception as exc:   # noqa: BLE001 - any failure is the owner's to read, never a crash
        reason = explain(exc)
        logger.warning("Outreach email to %s not sent: %s", to, exc)
        _note(problem=reason[:300])
        raise SendError(reason) from exc
    if row.problem:
        _note(problem="")


def send_test(subject, body):
    """An email to the owner's own inbox, to see how it arrives. Not recorded against any shop."""
    row = OutreachMail.current()
    send(row.inbox, f"[Test] {subject}", body, row)
    return row.inbox
