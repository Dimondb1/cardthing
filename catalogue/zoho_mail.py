"""
Zoho Mail's web API: every email the site sends can go out through the owner's own Zoho mailbox instead of ZeptoMail.
It goes over the web (HTTPS), which the server's host does not block, unlike the mail ports the mailbox's SMTP needs
(inbox/sending.py). The owner, 14 Nov 2026, with ZeptoMail refusing every email until it reviews the account: "can you
look into whether we can just move the outgoing emails onto Zoho while we know that works".

Connecting uses a Self Client in Zoho's API Console. Its Client ID and Client Secret, and a code made there with SCOPES,
are typed into Admin, Email sending (web/email_admin.py), never into a message. The code is swapped at once for a
refresh token, which does not expire, and the mailbox's account and the addresses it may send from are read. All of it
is kept in one file only the site can read (RIPRAPTOR_ZOHO_MAIL_FILE), with whether site email goes this way and the
address it goes from. An access token lasts an hour and is kept in the shared cache until shortly before then.

Zoho's API takes no display name, Reply-To or extra headers: the name shown is the one set for that address in Zoho
Mail, replies come back to that address, and the unsubscribe link is in each email's own words.
"""

import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .mail import MailError

# Where the owner's Zoho account is: (label, sign-in server, Zoho Mail's API server). Zoho's data centres
# (zoho.com/accounts/protocol/oauth/multi-dc.html); the address bar of Zoho Mail says which.
REGIONS = {
    "eu": ("Europe: mail.zoho.eu", "accounts.zoho.eu", "mail.zoho.eu"),
    "uk": ("United Kingdom: mail.zoho.uk", "accounts.zoho.uk", "mail.zoho.uk"),
    "com": ("United States: mail.zoho.com", "accounts.zoho.com", "mail.zoho.com"),
    "in": ("India: mail.zoho.in", "accounts.zoho.in", "mail.zoho.in"),
    "au": ("Australia: mail.zoho.com.au", "accounts.zoho.com.au", "mail.zoho.com.au"),
    "jp": ("Japan: mail.zoho.jp", "accounts.zoho.jp", "mail.zoho.jp"),
    "ca": ("Canada: mail.zohocloud.ca", "accounts.zohocloud.ca", "mail.zohocloud.ca"),
    "sa": ("Saudi Arabia: mail.zoho.sa", "accounts.zoho.sa", "mail.zoho.sa"),
}
# What the code made in the API Console must allow: sending, and reading the mailbox's account and addresses.
SCOPES = "ZohoMail.messages.CREATE,ZohoMail.accounts.READ"
TIMEOUT = 20
# An access token is asked for again this many seconds before Zoho says it ends.
TOKEN_MARGIN = 300

# What Zoho's sign-in server says when it will not swap a code or a refresh token, for the owner.
TOKEN_ERRORS = {
    "invalid_code": "Zoho did not accept the code. Codes last only a few minutes and work once: make a new one and "
                    "connect again straight away",
    "invalid_client": "Zoho did not accept the Client ID. Check it, and that the region is where your Zoho account "
                      "is",
    "invalid_client_secret": "Zoho did not accept the Client Secret. Copy it again from the API Console",
    "invalid_redirect_uri": "Zoho did not accept the request. Use a Self Client in the API Console",
}


# The saved file -----------------------------------------------------------------------------------------------

def path():
    name = settings.RIPRAPTOR_ZOHO_MAIL_FILE
    return Path(name) if name else None


_kept = {"stamp": None, "data": {}}


def saved():
    """What was saved from Admin, read again only when the file changes: every page asks whether email is on."""
    where = path()
    if where is None:
        return {}
    try:
        found = where.stat()
    except OSError:
        return {}
    stamp = (str(where), found.st_ino, found.st_mtime_ns, found.st_size)
    if _kept["stamp"] != stamp:
        try:
            data = json.loads(where.read_text())
        except (OSError, ValueError):
            data = {}
        _kept.update(stamp=stamp, data=data if isinstance(data, dict) else {})
    return dict(_kept["data"])


def write(data):
    """Write the file where only the site's user can read it, whole or not at all."""
    where = path()
    if where is None:
        raise MailError("Zoho Mail cannot be saved: RIPRAPTOR_ZOHO_MAIL_FILE is empty.")
    where.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=where.parent, prefix=".zoho-mail-")
    try:
        os.chmod(temp, 0o600)
        os.write(handle, json.dumps(data).encode())
        os.fsync(handle)
        os.close(handle)
        os.replace(temp, where)
    except BaseException:
        try:
            os.close(handle)
        except OSError:
            pass
        if os.path.exists(temp):
            os.unlink(temp)
        raise
    _kept["stamp"] = None


def connected(data=None):
    data = saved() if data is None else data
    return bool(data.get("region") in REGIONS and data.get("client_id") and data.get("client_secret")
                and data.get("refresh_token") and data.get("account_id") and data.get("from_address"))


def in_use():
    """Whether the site's emails go through Zoho Mail now: connected, and the owner chose it."""
    data = saved()
    return bool(data.get("use")) and connected(data)


def use(on, from_address=None):
    """Send the site's emails through Zoho Mail, or back through ZeptoMail, from one of the mailbox's addresses."""
    data = saved()
    if not connected(data):
        raise MailError("Connect Zoho Mail first.")
    if from_address is not None:
        if from_address.lower() not in [address.lower() for address in data.get("addresses", [])]:
            raise MailError(f"Zoho Mail cannot send from {from_address}: choose one of the mailbox's addresses.")
        data["from_address"] = from_address
    data["use"] = bool(on)
    write(data)


def forget():
    """Disconnect: the file goes, and site email is back on ZeptoMail."""
    where = path()
    if where is not None:
        try:
            where.unlink()
        except FileNotFoundError:
            pass
    _kept["stamp"] = None
    cache.delete(TOKEN_KEY)


# Talking to Zoho ----------------------------------------------------------------------------------------------

TOKEN_KEY = "zohomail:token"


def _open(request, opener):
    """(status, JSON answer) for a request, an HTTP error included; MailError when Zoho cannot be reached."""
    try:
        with (opener or urllib.request.urlopen)(request, timeout=TIMEOUT) as response:
            status, raw = getattr(response, "status", 200), response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except OSError as exc:
        raise MailError(f"Zoho Mail could not be reached: {exc}") from exc
    try:
        answer = json.loads(raw or b"{}")
    except ValueError:
        answer = {"text": raw.decode("utf-8", "ignore")[:300]}
    return status, answer if isinstance(answer, dict) else {"data": answer}


def token_request(region, fields, opener=None):
    """Ask Zoho's sign-in server for tokens, the way its documentation shows: a POST with the fields in the address
    (zoho.com/accounts/protocol/oauth/self-client/authorization-code-flow.html). It goes over HTTPS to Zoho alone, and
    the address is never logged here."""
    url = f"https://{REGIONS[region][1]}/oauth/v2/token?" + urllib.parse.urlencode(fields)
    request = urllib.request.Request(url, data=b"", method="POST", headers={"Accept": "application/json"})
    status, answer = _open(request, opener)
    if answer.get("error") or not answer.get("access_token"):
        error = str(answer.get("error") or f"answer {status}")
        raise MailError(TOKEN_ERRORS.get(error, f"Zoho would not sign in ({error[:80]})") + ".")
    return answer


def api(region, method, path_, token, body=None, opener=None):
    """(status, answer) from Zoho Mail's API."""
    url = f"https://{REGIONS[region][2]}/api/{path_}"
    request = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None, method=method,
        headers={"Authorization": f"Zoho-oauthtoken {token}", "Accept": "application/json",
                 **({"Content-Type": "application/json"} if body is not None else {})})
    return _open(request, opener)


def said(answer):
    """What Zoho Mail's answer says went wrong, briefly."""
    status = answer.get("status") if isinstance(answer.get("status"), dict) else {}
    data = answer.get("data") if isinstance(answer.get("data"), dict) else {}
    words = data.get("moreInfo") or status.get("description") or answer.get("text") or ""
    return str(words)[:200]


def ok(status, answer):
    code = answer.get("status", {}).get("code") if isinstance(answer.get("status"), dict) else None
    return 200 <= status < 300 and (not isinstance(code, int) or 200 <= code < 300)


def token_key(data):
    """The cache key for an access token, tied to the connection, so a new one never uses an old token."""
    return TOKEN_KEY + ":" + hashlib.sha256(data.get("refresh_token", "").encode()).hexdigest()[:16]


def access_token(data, opener=None, fresh=False):
    key = token_key(data)
    kept = None if fresh else cache.get(key)
    if kept:
        return kept
    try:
        answer = token_request(data["region"], {
            "grant_type": "refresh_token", "client_id": data["client_id"], "client_secret": data["client_secret"],
            "refresh_token": data["refresh_token"]}, opener)
    except MailError as exc:
        raise MailError(f"Zoho Mail no longer accepts the connection ({exc}) Connect it again on Admin, Email "
                        "sending.") from exc
    seconds = int(answer.get("expires_in") or 3600)
    cache.set(key, answer["access_token"], max(60, seconds - TOKEN_MARGIN))
    return answer["access_token"]


def addresses_of(account):
    """The addresses an account may send from: its own first, then those set up to send as, then its aliases."""
    found = [account.get("primaryEmailAddress") or account.get("mailboxAddress") or ""]
    for detail in account.get("sendMailDetails") or []:
        if isinstance(detail, dict) and detail.get("status") is not False:
            found.append(detail.get("fromAddress") or "")
    for email in account.get("emailAddress") or []:
        if isinstance(email, dict) and email.get("isConfirmed") is not False:
            found.append(email.get("mailId") or "")
    seen, addresses = set(), []
    for address in found:
        address = address.strip()
        if "@" in address and address.lower() not in seen:
            seen.add(address.lower())
            addresses.append(address)
    return addresses


def connect(region, client_id, client_secret, code, opener=None, now=None):
    """Swap a Self Client's code for a lasting connection to the owner's mailbox and save it. Keeps whether site email
    goes this way, and the address it goes from while the mailbox can still send from it. Returns what was saved."""
    if region not in REGIONS:
        raise MailError("Choose where your Zoho account is.")
    client_id, client_secret, code = client_id.strip(), client_secret.strip(), code.strip()
    if not (client_id and client_secret and code):
        raise MailError("Type the Client ID, the Client Secret and a new code.")
    tokens = token_request(region, {"grant_type": "authorization_code", "client_id": client_id,
                                    "client_secret": client_secret, "code": code}, opener)
    if not tokens.get("refresh_token"):
        raise MailError("Zoho gave no lasting connection. Make the code in a Self Client, with the scopes shown, and "
                        "connect again.")
    status, answer = api(region, "GET", "accounts", tokens["access_token"], opener=opener)
    if not ok(status, answer):
        raise MailError(f"Zoho Mail would not list your mailbox ({said(answer) or status}). Check the code was made "
                        f"with the scopes {SCOPES}.")
    accounts = [account for account in answer.get("data") or [] if isinstance(account, dict)]
    account = next((account for account in accounts if account.get("type", "ZOHO_ACCOUNT") == "ZOHO_ACCOUNT"),
                   accounts[0] if accounts else None)
    addresses = addresses_of(account or {})
    if account is None or not account.get("accountId") or not addresses:
        raise MailError("Zoho Mail found no mailbox on that Zoho account.")
    before = saved()
    wanted = before.get("from_address") or settings.RIPRAPTOR_MAIL_FROM
    from_address = next((address for address in addresses if address.lower() == wanted.lower()), addresses[0])
    data = {
        "region": region, "client_id": client_id, "client_secret": client_secret,
        "refresh_token": tokens["refresh_token"], "account_id": str(account["accountId"]),
        "mailbox": addresses[0], "addresses": addresses, "from_address": from_address,
        "use": bool(before.get("use")), "connected_at": (now or timezone.now()).isoformat(),
    }
    cache.delete(token_key(before))
    write(data)
    cache.set(token_key(data), tokens["access_token"], max(60, int(tokens.get("expires_in") or 3600) - TOKEN_MARGIN))
    return data


def send(to, subject, text, html, bcc="", sender="", opener=None):
    """Send one email from the mailbox: from ``sender`` when the mailbox may send from it, else the address chosen in
    Admin. Raises MailError when Zoho Mail refuses it or cannot be reached. A token Zoho no longer takes is renewed
    once."""
    data = saved()
    if not connected(data):
        raise MailError("Zoho Mail is not connected: connect it on Admin, Email sending.")
    allowed = {address.lower(): address for address in data.get("addresses", [])}
    from_address = allowed.get((sender or "").lower(), data["from_address"])
    body = {"fromAddress": from_address, "toAddress": to, "subject": subject, "content": html or text,
            "mailFormat": "html" if html else "plaintext", "askReceipt": "no", "encoding": "UTF-8"}
    if bcc:
        body["bccAddress"] = bcc
    path_ = f"accounts/{data['account_id']}/messages"
    status, answer = api(data["region"], "POST", path_, access_token(data, opener), body, opener)
    if status == 401:
        status, answer = api(data["region"], "POST", path_, access_token(data, opener, fresh=True), body, opener)
    if not ok(status, answer):
        raise MailError(f"Zoho Mail refused the email ({status}): {said(answer) or 'no reason given'}")
    return answer
