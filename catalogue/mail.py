"""
Send one email through Zoho ZeptoMail's API, or through the owner's Zoho mailbox (catalogue/zoho_mail.py) when that is
switched on in Admin, Email sending.

ZeptoMail sends and keeps no inbox, which is all back-in-stock emails need.
The API address differs by Zoho data centre, so it is a setting: copy it,
with the token, from the Mail Agent's API page in ZeptoMail.

ZeptoMail sends nothing from the site until it has reviewed the account (its code SM_128), whatever its trial
allowance says; its refusals are put in plain words (REFUSALS), so the owner can tell the review from a bad token.
"""

import json
import re
import urllib.error
import urllib.request

from django.conf import settings


class MailError(Exception):
    pass


# ZeptoMail's error codes (zoho.com/zeptomail/help/api/error-codes.html) the owner can act on, in plain words.
REFUSALS = {
    "SM_128": "ZeptoMail has not finished reviewing your account, so it sends nothing from the site yet",
    "SM_133": "ZeptoMail's trial allowance is used up until it finishes reviewing your account",
    "SMI_115": "ZeptoMail's limit for today is used up. It sends again tomorrow",
    "SM_151": "the daily limit set on the Mail Agent in ZeptoMail is used up",
}
CODE = re.compile(r"\b[A-Z]{2,4}_\d{3,4}\b")


def refusal(text):
    """The plain words for the first ZeptoMail code in ``text`` that has them, or ""."""
    return next((REFUSALS[code] for code in CODE.findall(text or "") if code in REFUSALS), "")


def zeptomail_ready():
    return bool(settings.RIPRAPTOR_ZEPTOMAIL_TOKEN and settings.RIPRAPTOR_MAIL_FROM)


def through_zoho():
    from . import zoho_mail

    return zoho_mail.in_use()


def enabled():
    return zeptomail_ready() or through_zoho()


def send(to, subject, text, html, opener=None, unsubscribe="", sender=None, reply_to="", bcc="", token="",
         tracking=None):
    """Send one message. Raises MailError when ZeptoMail refuses it or cannot be reached.

    The alert emails give only the first five. The shop emails (inbox/sending.py) also give ``sender`` (address,
    name), ``reply_to``, ``bcc`` (a copy), a ``token`` of their own if one is saved, and ``tracking=False``; an empty
    ``html`` sends the plain text alone. Through Zoho Mail, the mailbox's own name and address are used unless
    ``sender`` is one it may send from, and there is no Reply-To, tracking or unsubscribe header to set."""
    if through_zoho():
        from . import zoho_mail

        return zoho_mail.send(to, subject, text, html, bcc=bcc, sender=(sender or ("", ""))[0], opener=opener)
    token = token or settings.RIPRAPTOR_ZEPTOMAIL_TOKEN
    address, name = sender or (settings.RIPRAPTOR_MAIL_FROM, settings.RIPRAPTOR_SITE_NAME)
    if not (token and address):
        raise MailError("Email is not set up: RIPRAPTOR_ZEPTOMAIL_TOKEN is needed.")
    if not token.lower().startswith("zoho-enczapikey"):
        token = f"Zoho-enczapikey {token}"
    body = {
        "from": {"address": address, "name": name},
        "to": [{"email_address": {"address": to}}],
        "subject": subject,
        "textbody": text,
    }
    if html:
        body["htmlbody"] = html
    if reply_to:
        body["reply_to"] = [{"address": reply_to}]   # flat, unlike to and bcc: ZeptoMail's own shape
    if bcc:
        body["bcc"] = [{"email_address": {"address": bcc}}]
    if tracking is not None:
        body["track_opens"] = body["track_clicks"] = tracking
    if unsubscribe:
        # Lets Gmail and Apple Mail show their own Unsubscribe link next to the sender.
        body["mime_headers"] = {"List-Unsubscribe": f"<{unsubscribe}>"}
    request = urllib.request.Request(
        settings.RIPRAPTOR_ZEPTOMAIL_URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": token, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with (opener or urllib.request.urlopen)(request, timeout=15) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "ignore")[:300]
        plain = refusal(text)
        raise MailError(f"ZeptoMail said {exc.code}: {plain + '. ' if plain else ''}{text}") from exc
    except OSError as exc:
        raise MailError(f"ZeptoMail could not be reached: {exc}") from exc
