"""
The six-digit codes an authenticator app (Google Authenticator, Microsoft Authenticator and the like) shows,
as RFC 6238 sets them out: a new code every 30 seconds, worked out from a secret the app and the site share.
The standard library is enough, so no package is added for it.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP = 30
DIGITS = 6
# A code from the step before or after also counts, for a phone clock a little out and a code typed as it changed.
DRIFT = 1


def new_secret():
    """A fresh 160-bit secret, in the base32 letters an app's "enter a setup key" box takes."""
    return base64.b32encode(secrets.token_bytes(20)).decode()


def grouped(secret):
    """The secret in groups of four, easier to type into an app by hand."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def code_at(secret, step):
    """The code for one 30-second step."""
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def current_step(now=None):
    return int((time.time() if now is None else now) // STEP)


def matching_step(secret, code, after=0, now=None):
    """The step ``code`` belongs to, near now and later than ``after`` (so a code works once), or None."""
    code = "".join(code.split())
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in range(step - DRIFT, step + DRIFT + 1):
        if candidate > after and hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def app_link(secret, account, issuer):
    """The otpauth:// link that adds the account to an authenticator app in one tap, on the phone that has it."""
    label = quote(f"{issuer}:{account}")
    query = urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS, "period": STEP})
    return f"otpauth://totp/{label}?{query}"
