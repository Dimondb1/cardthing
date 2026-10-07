"""
Send one email through Zoho ZeptoMail's API.

ZeptoMail sends and keeps no inbox, which is all back-in-stock emails need.
The API address differs by Zoho data centre, so it is a setting: copy it,
with the token, from the Mail Agent's API page in ZeptoMail.
"""

import json
import urllib.error
import urllib.request

from django.conf import settings


class MailError(Exception):
    pass


def enabled():
    return bool(settings.RIPRAPTOR_ZEPTOMAIL_TOKEN and settings.RIPRAPTOR_MAIL_FROM)


def send(to, subject, text, html, opener=None, unsubscribe=""):
    """Send one message. Raises MailError when ZeptoMail refuses it or cannot be reached."""
    if not enabled():
        raise MailError("Email is not set up: RIPRAPTOR_ZEPTOMAIL_TOKEN is needed.")
    token = settings.RIPRAPTOR_ZEPTOMAIL_TOKEN
    if not token.lower().startswith("zoho-enczapikey"):
        token = f"Zoho-enczapikey {token}"
    body = {
        "from": {"address": settings.RIPRAPTOR_MAIL_FROM, "name": settings.RIPRAPTOR_SITE_NAME},
        "to": [{"email_address": {"address": to}}],
        "subject": subject,
        "textbody": text,
        "htmlbody": html,
    }
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
        raise MailError(f"ZeptoMail said {exc.code}: {exc.read().decode('utf-8', 'ignore')[:300]}") from exc
    except OSError as exc:
        raise MailError(f"ZeptoMail could not be reached: {exc}") from exc
